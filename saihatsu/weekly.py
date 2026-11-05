"""週次の自動実行 `saihatsu weekly`。

会話記録の置き場から、前回の実行以降に更新された記録を読み、
extract → add → (新しく足した規則だけ)前後比較 → 効いた規則だけを共通メモリへ書き出す。

機械ごとの設定(会話記録の置き場・出力先のパス)は、公開しないローカル設定ファイル
`saihatsu.local.toml` に書く(例は `saihatsu.local.toml.example`)。会話記録・規則帳・週次の結果は、
このリポジトリには入れない。
"""
from __future__ import annotations

import json
import time
import tomllib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from . import core, llm
from .conversation import clip_turns, frontmatter_value, parse_conversation
from .rules import Rule, export_text, load_rules, save_rules

UNCHECKED, EFFECTIVE, INEFFECTIVE, UNDECIDABLE = "未確認", "効果あり", "効果なし", "判定不能"
CHECK_REPEAT = 3
CALLS_PER_CHECK = 4 * CHECK_REPEAT  # 1回の前後比較 = 出し直し2 + 判定2。これを繰り返す回数ぶん
MAX_CONSECUTIVE_FAILURES = 3
SHARED_LINK = "再発防止の規則"
FILE_HEAD_BYTES = 2000


@dataclass
class Config:
    vault: Path
    sources: list[str] = field(default_factory=lambda: ["_人工知能会話原本/claude"])
    rulebook: str = "_Mistakes/再発防止帳/規則帳.md"
    weekly_dir: str = "_Mistakes/再発防止帳"
    shared_memory_dir: str = "_人工知能共通メモリ"
    shared_file: str = "再発防止の規則.md"
    state: Path = Path("saihatsu.state.json")
    model: str = "claude-haiku-5-5"
    max_calls: int = 300
    initial_days: int = 7
    lookback_days: int = 3  # 同期の遅れを見込んで、前回から何日戻って探すか(処理済みの記録は飛ばす)
    clip_chars: int = 1500
    max_export: int = 20


def load_config(path: str | Path) -> Config:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"設定ファイルがありません: {p}(saihatsu.local.toml.example をコピーして作ってください)"
        )
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    if "vault" not in data:
        raise ValueError(f"設定ファイル {p} に vault(会話記録の置き場がある保管庫のパス)がありません。")
    vault = Path(data["vault"])
    if not vault.is_dir():
        raise FileNotFoundError(f"vault のフォルダが見つかりません: {vault}")
    cfg = Config(vault=vault)
    for k in ("sources", "rulebook", "weekly_dir", "shared_memory_dir", "shared_file", "model",
              "max_calls", "initial_days", "lookback_days", "clip_chars", "max_export"):
        if k in data:
            setattr(cfg, k, data[k])
    state = Path(data.get("state", "saihatsu.state.json"))
    cfg.state = state if state.is_absolute() else p.resolve().parent / state
    return cfg


# ---- 状態ファイル(ローカル。公開しない) ----

def load_state(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"last_run": None, "processed": [], "pending": []}


def save_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


# ---- 対象の会話記録を選ぶ ----

def _parse_time(s: str) -> datetime:
    t = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _updated(path: Path) -> datetime:
    """会話記録の更新日時。情報欄の `updated:` を使う(同期でファイルの更新日時が揃ってしまうため)。"""
    with open(path, "rb") as f:
        head = f.read(FILE_HEAD_BYTES).decode("utf-8", errors="replace")
    v = frontmatter_value(head, "updated")
    if v:
        try:
            return _parse_time(v)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def find_candidates(cfg: Config, state: dict, now: datetime) -> list[tuple[Path, datetime]]:
    last = state.get("last_run")
    since = _parse_time(last) - timedelta(days=cfg.lookback_days) if last else now - timedelta(days=cfg.initial_days)
    # ファイル名の先頭の日付(作成日)が古すぎるものは、中身を開かずに飛ばす(全件の読み込みを避ける)
    name_floor = (since - timedelta(days=60)).date().isoformat()
    done = set(state.get("processed", []))
    found = []
    for src in cfg.sources:
        folder = cfg.vault / src
        if not folder.is_dir():
            continue
        for f in sorted(folder.glob("*.md")):
            if f.name[:10] < name_floor or f.name in done:
                continue
            u = _updated(f)
            if u > since:
                found.append((f, u))
    return sorted(found, key=lambda x: x[1])


# ---- 効果の状態 ----

def decide_status(rows: list[dict]) -> str:
    """前後比較の行から効果の状態を決める。規則なしで再発が0なら、比べようがないので判定不能。"""
    wo = [r["recurred_without"] for r in rows if r["recurred_without"] is not None]
    wi = [r["recurred_with"] for r in rows if r["recurred_with"] is not None]
    if not wo or not wi or sum(wo) == 0:
        return UNDECIDABLE
    return EFFECTIVE if sum(wi) / len(wi) < sum(wo) / len(wo) else INEFFECTIVE


# ---- 書き出し ----

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def render_shared(rules: list[Rule], now: datetime, max_export: int) -> str:
    good = sorted((r for r in rules if r.status == EFFECTIVE), key=lambda r: (-r.count, r.id))[:max_export]
    head = (
        f"---\ndate: {now.date().isoformat()}\nstatus: current\ntags: [再発防止帳, 自動生成]\n"
        f"related: [\"[[MEMORY]]\"]\n---\n\n"
        "> 再発防止帳が自動で作る。手で直さない。毎週の実行のたびに作り直す。\n\n"
        "# 再発防止の規則\n\n"
        "各人工知能は、次の規則を守る。過去の会話で本人が苛立った誤りから作り、"
        "規則なし・ありの前後比較で再発が減った規則だけを載せている(発生回数の多い順、最大"
        f"{max_export}件)。\n\n"
    )
    if not good:
        return head + "(前後比較で効果が確かめられた規則は、まだありません。)\n"
    return head + "\n".join(f"{i}. {r.rule}" for i, r in enumerate(good, 1)) + "\n"


def ensure_memory_link(memory_md: Path) -> bool:
    """MEMORY.md に、共通の規則ファイルへのリンクがなければ1行だけ追記する。足したら True。"""
    if not memory_md.exists():
        return False
    text = memory_md.read_text(encoding="utf-8")
    if f"[[{SHARED_LINK}" in text:
        return False
    line = f"- [[{SHARED_LINK}]] — 再発防止帳が自動で作る。前後比較で効いた規則だけ(最大20件、毎週日曜に更新。手で直さない)\n"
    with open(memory_md, "a", encoding="utf-8", newline="\n") as f:
        f.write(("" if text.endswith("\n") else "\n") + line)
    return True


def _name(r: Rule) -> str:
    return f"{r.id}「{r.error_type or r.rule[:20]}」"


def _names(rules: list[Rule]) -> str:
    return "、".join(_name(r) for r in rules) if rules else "なし"


@dataclass
class Result:
    conversations: list[tuple[str, int, int]] = field(default_factory=list)  # (ファイル名, 取り出した組, 新しい規則)
    new_rules: list[Rule] = field(default_factory=list)
    checked: dict[str, list[dict]] = field(default_factory=dict)  # 規則ID -> 前後比較の行
    effective: list[Rule] = field(default_factory=list)
    ineffective: list[Rule] = field(default_factory=list)  # 効果なし + 判定不能
    unchecked: list[Rule] = field(default_factory=list)  # 呼び出し上限で次回へ回した
    errors: list[str] = field(default_factory=list)
    stopped: str = ""  # 途中で止めた理由
    calls: int = 0
    seconds: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    note_path: Path | None = None


def render_note(res: Result, rules: list[Rule], cfg: Config, now: datetime) -> str:
    by_id = {r.id: r for r in rules}
    lines = [
        f"---\ndate: {now.date().isoformat()}\nstatus: current\ntags: [再発防止帳, 週次]\n"
        f"related: [\"[[規則帳]]\", \"[[{SHARED_LINK}]]\"]\n---\n",
        f"# 再発防止帳 週次({now.strftime('%y%m%d')})\n",
        f"- 増えた規則:{len(res.new_rules)}件({_names(res.new_rules)})",
        f"- 効いた規則:{len(res.effective)}件({_names(res.effective)})",
        f"- 効かなかった・判定不能の規則:{len(res.ineffective)}件({_names(res.ineffective)})",
        "",
    ]
    if res.unchecked:
        lines += [f"前後比較を次回へ回した規則:{_names(res.unchecked)}", ""]
    if res.stopped:
        lines += [f"途中で止めた理由:{res.stopped}", ""]
    lines += ["## 対象の会話", ""]
    if res.conversations:
        lines += [f"- {n}:苛立ちの組 {a} 件、新しい規則 {b} 件" for n, a, b in res.conversations]
    else:
        lines.append("- なし(前回の実行以降に更新された会話記録がなかった)")
    if res.errors:
        lines += ["", "## 失敗", ""] + [f"- {e}" for e in res.errors]
    lines += ["", "## 前後比較", ""]
    if res.checked:
        for rid, rows in res.checked.items():
            r = by_id.get(rid)
            lines += [f"### {_name(r) if r else rid}(効果の状態:{r.status if r else '?'})", "",
                      f"規則:{r.rule if r else ''}", "", core.render_check_table(rows)]
    else:
        lines.append("今週は前後比較を行わなかった。")
    lines += [
        "## 呼び出し", "",
        f"- `claude -p` の呼び出し:{res.calls} 回(上限 {cfg.max_calls} 回)",
        f"- 所要時間:{res.seconds:.0f} 秒",
        f"- トークン:入力 {res.tokens_in}、出力 {res.tokens_out}",
        f"- モデル:{cfg.model}",
        "",
    ]
    return "\n".join(lines)


def _note_path(cfg: Config, now: datetime) -> Path:
    base = cfg.vault / cfg.weekly_dir
    p = base / f"{now.strftime('%y%m%d')}_週次.md"
    # 同じ日に2回目以降の実行でも、既存のノートを上書きしない
    return p if not p.exists() else base / f"{now.strftime('%y%m%d_%H%M')}_週次.md"


# ---- 本体 ----

def run_weekly(
    cfg: Config, ask: Callable[..., str] = llm.ask, now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> Result:
    now = now or datetime.now(timezone.utc).astimezone()
    t0 = time.time()
    u0 = (llm.USAGE.input_tokens, llm.USAGE.output_tokens)
    calls = [0]

    def counted(*a, **k):
        calls[0] += 1
        return ask(*a, **k)

    def left() -> int:
        return cfg.max_calls - calls[0]

    state = load_state(cfg.state)
    rulebook = cfg.vault / cfg.rulebook
    rulebook.parent.mkdir(parents=True, exist_ok=True)
    res = Result()
    all_complete = True

    def do_check(rule: Rule, turns, item) -> None:
        rows = core.check(turns, rulebook, cfg.model, [item], ask=counted, repeat=CHECK_REPEAT,
                          rule_text=export_text([rule]))
        rule.status = decide_status(rows)
        res.checked[rule.id] = rows
        (res.effective if rule.status == EFFECTIVE else res.ineffective).append(rule)
        log(f"  前後比較 {_name(rule)} → {rule.status}")

    def prepared(path: Path):
        return clip_turns(parse_conversation(path.read_text(encoding="utf-8")), cfg.clip_chars)

    # 1. 前回、呼び出し上限で後回しにした前後比較
    still_pending = []
    for p in state.get("pending", []):
        rules = load_rules(rulebook)
        rule = next((r for r in rules if r.id == p["rule_id"]), None)
        if rule is None:
            continue
        if left() < CALLS_PER_CHECK:
            still_pending.append(p)
            continue
        try:
            do_check(rule, prepared(Path(p["source_path"])), p["item"])
            save_rules(rulebook, rules)
        except (OSError, ValueError, llm.ClaudeCodeError) as ex:
            res.errors.append(f"前回分の前後比較 {p['rule_id']}:{ex}")
            still_pending.append(p)
    state["pending"] = still_pending

    # 2. 新しい会話記録
    failures = 0
    for path, _updated_at in find_candidates(cfg, state, now):
        if left() < 2:
            res.stopped = f"呼び出し上限({cfg.max_calls} 回)に近いため、残りを次回へ回した。"
            all_complete = False
            break
        log(f"読む: {path.name}")
        try:
            turns = prepared(path)
            if not any(t.role == "user" for t in turns):
                state["processed"].append(path.name)
                continue
            items = core.extract(turns, cfg.model, counted)
            if left() < len(items):  # 規則を足す(重複判定)ぶんが足りなければ、この記録は次回へ
                res.stopped = f"呼び出し上限({cfg.max_calls} 回)に近いため、残りを次回へ回した。"
                all_complete = False
                break
            added = core.add(items, rulebook, cfg.model, counted, source=path.name) if items else []
        except (OSError, ValueError, KeyError, TypeError, llm.ClaudeCodeError) as ex:
            failures += 1
            res.errors.append(f"{path.name}:{ex}")
            log(f"  失敗: {ex}")
            all_complete = False
            if failures >= MAX_CONSECUTIVE_FAILURES:
                res.stopped = f"{MAX_CONSECUTIVE_FAILURES} 回続けて失敗したため止めた。"
                break
            continue
        failures = 0
        state["processed"].append(path.name)
        new_entries = [(it, a) for it, a in zip(items, added) if a["created"]]
        res.conversations.append((path.name, len(items), len(new_entries)))
        log(f"  組 {len(items)} 件、新しい規則 {len(new_entries)} 件")
        rules = load_rules(rulebook)
        for it, a in new_entries:
            rule = next(r for r in rules if r.id == a["id"])
            rule.status = UNCHECKED
            res.new_rules.append(rule)
            if left() >= CALLS_PER_CHECK:
                try:
                    do_check(rule, turns, it)
                except (ValueError, KeyError, TypeError, llm.ClaudeCodeError) as ex:
                    res.errors.append(f"前後比較 {rule.id}:{ex}")
                    rule.status = UNCHECKED
                    state["pending"].append({"rule_id": rule.id, "source_path": str(path), "item": it})
            else:
                state["pending"].append({"rule_id": rule.id, "source_path": str(path), "item": it})
        save_rules(rulebook, rules)

    rules = load_rules(rulebook)
    status_now = {r.id: r.status for r in rules}
    res.unchecked = [r for r in rules if r.id in {p["rule_id"] for p in state["pending"]}]
    # 規則帳の最新の状態を、結果の一覧にそろえる
    res.new_rules = [r for r in rules if r.id in {x.id for x in res.new_rules}]
    res.effective = [r for r in rules if r.id in {x.id for x in res.effective} and status_now[r.id] == EFFECTIVE]
    res.ineffective = [r for r in rules if r.id in {x.id for x in res.ineffective}]

    # 3. 共通メモリへ(効いた規則だけ)・MEMORY.md のリンク・週次ノート
    shared_dir = cfg.vault / cfg.shared_memory_dir
    _write(shared_dir / cfg.shared_file, render_shared(rules, now, cfg.max_export))
    if ensure_memory_link(shared_dir / "MEMORY.md"):
        log("MEMORY.md にリンクを1行足した。")
    res.calls = calls[0]
    res.seconds = time.time() - t0
    res.tokens_in = llm.USAGE.input_tokens - u0[0]
    res.tokens_out = llm.USAGE.output_tokens - u0[1]
    res.note_path = _note_path(cfg, now)
    _write(res.note_path, render_note(res, rules, cfg, now))

    # 4. 状態。最後まで終えたときだけ、前回の実行日時を進める
    if all_complete:
        state["last_run"] = now.astimezone(timezone.utc).isoformat()
    state["processed"] = state["processed"][-2000:]
    save_state(cfg.state, state)
    return res


def summary_text(res: Result, cfg: Config) -> str:
    return (
        f"週次の実行を終えた。会話 {len(res.conversations)} 件、増えた規則 {len(res.new_rules)} 件、"
        f"効いた {len(res.effective)} 件、効かなかった・判定不能 {len(res.ineffective)} 件、"
        f"次回へ回した {len(res.unchecked)} 件。\n"
        f"呼び出し {res.calls}/{cfg.max_calls} 回、{res.seconds:.0f} 秒。\n"
        f"週次ノート:{res.note_path}\n" + (f"止めた理由:{res.stopped}\n" if res.stopped else "")
    )
