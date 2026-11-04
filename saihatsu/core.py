"""extract / add / check の本体。Claude の呼び出しは ask 引数で差し替えられる(試験用)。"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import llm
from .conversation import AI, USER, Turn, parse_conversation, render_turns
from .rules import Rule, add_or_count, export_text, load_rules, save_rules

Ask = Callable[..., str]

EXTRACT_SYSTEM = """あなたは、人工知能と利用者の会話記録を読み、再発防止の規則を作る分析者です。
会話の各発言には [番号] が付いています。
手順:
1. 利用者が苛立ち・不満・訂正・やり直しの要求を示した発言(user_turn)を探す。
2. その直前の人工知能の発言(ai_turn)のどこが誤り・不備だったかを特定する。
3. 誤りの種類(短い名詞句)、原因(1文)、再発防止の規則を書く。
規則は日本語の1行で、「〜する」「〜しない」の形にし、後から読んだ人が守ったかどうかを判定できる具体的な書き方にする。
根拠の引用(quote)は、利用者の苛立ちの発言から、そのまま抜き出す。
苛立ちがない会話では空の配列を返す。推測で組を作らない。
出力は JSON の配列のみ。各要素: {"user_turn": 番号, "ai_turn": 番号, "error_type": "...", "cause": "...", "rule": "...", "quote": "..."}"""

DEDUP_SYSTEM = """あなたは規則の重複を判定する係です。
新しい規則が、既存の規則のどれかと「守るべき行動として実質同じ(一方を守れば他方も満たされる)」なら、その既存規則の ID を返します。
言い回しが違っても意味が同じなら重複です。関連はあるが別の行動を求めるものは重複ではありません。
出力は JSON のみ: {"duplicate_of": "R001" または null, "reason": "..."}"""

JUDGE_SYSTEM = """あなたは、人工知能の応答に、過去と同じ誤りが再発したかを判定する審判です。
与えられるのは、過去の誤りの説明と、再発防止の規則と、新しく得た応答です。
応答が、その誤りと同じ種類の問題を含む場合だけ recurred を true にします。応答の良し悪し一般は判定しません。
出力は JSON のみ: {"recurred": true または false, "reason": "..."}"""


def extract(conv_text: str, model: str, ask: Ask = llm.ask) -> list[dict]:
    turns = parse_conversation(conv_text)
    if not turns:
        raise ValueError("会話記録から発言を読み取れませんでした(「## あなた」「## Claude」の見出しが必要です)。")
    raw = ask(EXTRACT_SYSTEM, render_turns(turns), model, 4000)
    data = llm.parse_json(raw)
    if isinstance(data, dict):
        data = [data]
    items = []
    for d in data:
        try:
            u, a = int(d["user_turn"]), int(d["ai_turn"])
        except (KeyError, TypeError, ValueError):
            continue
        if not (0 <= u < len(turns) and 0 <= a < len(turns)):
            continue
        # 利用者の苛立ちの発言(user_turn)は、人工知能の誤り(ai_turn)より後ろにある
        if turns[a].role != AI or turns[u].role != USER or u <= a:
            continue
        items.append(
            {
                "user_turn": u,
                "ai_turn": a,
                "error_type": str(d.get("error_type", "")).strip(),
                "cause": str(d.get("cause", "")).strip(),
                "rule": str(d.get("rule", "")).strip(),
                "quote": str(d.get("quote", "")).strip(),
            }
        )
    return [i for i in items if i["rule"]]


def add(items: list[dict], rules_path: str | Path, model: str, ask: Ask = llm.ask) -> list[dict]:
    """取り出し結果を規則帳に足す。重複なら発生回数+1。結果の一覧を返す。"""
    rules = load_rules(rules_path)
    log = []
    for it in items:
        dup = None
        if rules:
            listing = "\n".join(f"{r.id}: {r.rule}" for r in rules)
            raw = ask(DEDUP_SYSTEM, f"既存の規則:\n{listing}\n\n新しい規則:\n{it['rule']}", model, 500)
            try:
                dup = llm.parse_json(raw).get("duplicate_of")
            except Exception:
                dup = None
        new = Rule(
            id="", rule=it["rule"], error_type=it.get("error_type", ""),
            cause=it.get("cause", ""), quote=it.get("quote", ""),
        )
        r, created = add_or_count(rules, new, dup)
        log.append({"id": r.id, "rule": r.rule, "created": created, "count": r.count})
    save_rules(rules_path, rules)
    return log


def _replay_messages(turns: list[Turn], user_index: int) -> list[dict]:
    """指定した利用者の発言までを、会話の続きとして出し直す形にする。"""
    return [
        {"role": "user" if t.role == USER else "assistant", "content": t.text}
        for t in turns[: user_index + 1]
    ]


def check(
    conv_text: str, rules_path: str | Path, model: str, items: list[dict] | None = None,
    ask: Ask = llm.ask,
) -> list[dict]:
    """前後比較。苛立ちの直前の依頼を、規則なし・ありで出し直し、誤りが再発したかを判定する。"""
    turns = parse_conversation(conv_text)
    if items is None:
        items = extract(conv_text, model, ask)
    rules = load_rules(rules_path)
    rule_text = export_text(rules)
    rows = []
    for it in items:
        req = None
        for t in reversed(turns[: it["ai_turn"]]):
            if t.role == USER:
                req = t
                break
        if req is None:
            continue
        msgs = _replay_messages(turns, req.index)
        without = ask("", "", model, 1500, messages=msgs)
        with_rules = ask(rule_text, "", model, 1500, messages=msgs)
        verdicts = {}
        for label, resp in (("without", without), ("with", with_rules)):
            prompt = (
                f"過去の誤りの種類: {it['error_type']}\n原因: {it['cause']}\n再発防止の規則: {it['rule']}\n\n"
                f"利用者の依頼:\n{req.text}\n\n新しく得た応答:\n{resp}"
            )
            try:
                v = llm.parse_json(ask(JUDGE_SYSTEM, prompt, model, 500))
                rec = v.get("recurred")
                verdicts[label] = rec if isinstance(rec, bool) else None
            except Exception:
                verdicts[label] = None
        rows.append(
            {
                "error_type": it["error_type"], "rule": it["rule"],
                "recurred_without": verdicts["without"], "recurred_with": verdicts["with"],
            }
        )
    return rows


def _mark(v: bool | None) -> str:
    return "判定不能" if v is None else ("再発" if v else "再発せず")


def render_check_table(rows: list[dict]) -> str:
    lines = ["| 誤りの種類 | 規則 | 規則なし | 規則あり |", "|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['error_type']} | {r['rule']} | {_mark(r['recurred_without'])} | {_mark(r['recurred_with'])} |"
        )
    def tally(key: str) -> str:
        judged = [r[key] for r in rows if r[key] is not None]
        return f"{sum(judged)}/{len(judged)}"

    lines += ["", f"再発した数(判定できたもののみ): 規則なし {tally('recurred_without')} → 規則あり {tally('recurred_with')}"]
    return "\n".join(lines) + "\n"
