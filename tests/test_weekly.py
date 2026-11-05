"""週次の自動実行の試験。会話記録と保管庫は、試験用の架空の文面を一時フォルダに作る。Claude は偽物に差し替える。"""
import json
from datetime import datetime, timezone

from saihatsu import core, weekly
from saihatsu.conversation import AI, USER, frontmatter_value, parse_conversation
from saihatsu.rules import Rule, load_rules, save_rules

# 会話原本の形(日時つきの見出し、ツール呼び出しの囲み)。文面は架空
ORIGINAL = """---
uuid: 11111111
title: "試験用の会話"
created: 2026-11-04T09:00:00.000000Z
updated: 2026-11-04T09:10:00.000000Z
messages: 4
---

# 試験用の会話

## あなた (2026-11-04 09:00:00)

明日の天気を教えて

## Claude (2026-11-04 09:00:20)

調べます。

> [!abstract]- ツール呼び出し: search

> [!quote]- ツール結果: search
> {"text": "## あなた という文字列が、ツール結果の中に出てくる"}
> 2行目も囲みの中

晴れです(確認していません)。

## あなた (2026-11-04 09:05:00)

> 引用された行は、囲みではないので残る

確認してから言って。やり直し。
"""


def test_parse_original_format():
    turns = parse_conversation(ORIGINAL)
    assert [t.role for t in turns] == [USER, AI, USER]
    assert turns[0].text == "明日の天気を教えて"
    assert "ツール" not in turns[1].text and "2行目" not in turns[1].text
    assert turns[1].text == "調べます。\n\n晴れです(確認していません)。"
    assert turns[2].text.startswith("> 引用された行は")  # 囲みでない引用は本文として残る
    assert frontmatter_value(ORIGINAL, "updated") == "2026-11-04T09:10:00.000000Z"


def test_callout_inside_code_fence_is_kept():
    text = "## あなた\n```\n> [!note] コード内\n```\n\n## Claude\nはい\n"
    turns = parse_conversation(text)
    assert "> [!note] コード内" in turns[0].text and len(turns) == 2


def _row(without, with_):
    return {"error_type": "e", "rule": "r", "recurred_without": without, "recurred_with": with_}


def test_decide_status():
    assert weekly.decide_status([_row(True, False), _row(True, False), _row(False, False)]) == weekly.EFFECTIVE
    assert weekly.decide_status([_row(True, True), _row(True, True)]) == weekly.INEFFECTIVE
    assert weekly.decide_status([_row(False, False)] * 3) == weekly.UNDECIDABLE  # 規則なしで再発0
    assert weekly.decide_status([_row(None, None)]) == weekly.UNDECIDABLE
    assert weekly.decide_status([_row(True, None)]) == weekly.UNDECIDABLE


def test_rulebook_roundtrip_with_status_and_sources(tmp_path):
    p = tmp_path / "r.md"
    save_rules(p, [Rule("R001", "守る", "型", "原因", 2, "引用", status="効果あり", sources=["a.md", "b.md"])])
    r = load_rules(p)[0]
    assert (r.status, r.sources, r.count) == ("効果あり", ["a.md", "b.md"], 2)
    save_rules(p, [Rule("R001", "守る")])  # 付けない規則は、これまでと同じ形のまま
    assert "効果の状態" not in p.read_text(encoding="utf-8")


def _fake_ask(calls):
    def ask(system, user, model, max_tokens=0, messages=None):
        calls.append(system[:6])
        if system == core.EXTRACT_SYSTEM:
            return json.dumps([{"user_turn": 2, "ai_turn": 1, "error_type": "確認せずに断定",
                                "cause": "調べた結果を見ていない", "rule": "確認していない事実は断定しない",
                                "quote": "確認してから言って"}], ensure_ascii=False)
        if system == core.DEDUP_SYSTEM:
            return '{"duplicate_of": null}'
        if system == core.JUDGE_SYSTEM:
            return json.dumps({"recurred": "ミス応答" in user.split("新しく得た応答")[-1]})
        return "良い応答" if system else "ミス応答"  # 規則あり(システム文あり)なら誤りが出ない

    return ask


def _vault(tmp_path):
    v = tmp_path / "vault"
    conv = v / "conv"
    conv.mkdir(parents=True)
    (conv / "2026-11-04_new.md").write_text(ORIGINAL, encoding="utf-8")
    old = ORIGINAL.replace("2026-11-04T09:10:00", "2026-09-01T09:10:00")
    (conv / "2026-09-01_old.md").write_text(old, encoding="utf-8")
    mem = v / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# 索引\n\n- 既存の1行", encoding="utf-8")
    (tmp_path / "cfg.toml").write_text(f'vault = "{v.as_posix()}"\nsources = ["conv"]\nshared_memory_dir = "memory"\n'
                                       'rulebook = "mist/rules.md"\nweekly_dir = "mist"\nmax_calls = 300\n',
                                       encoding="utf-8")
    return v


NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)


def test_weekly_end_to_end(tmp_path):
    v = _vault(tmp_path)
    cfg = weekly.load_config(tmp_path / "cfg.toml")
    calls = []
    res = weekly.run_weekly(cfg, _fake_ask(calls), NOW, log=lambda m: None)

    assert [c[0] for c in res.conversations] == ["2026-11-04_new.md"]  # 7日より前の記録は読まない
    rules = load_rules(v / "mist" / "rules.md")
    assert len(rules) == 1 and rules[0].status == weekly.EFFECTIVE and rules[0].sources == ["2026-11-04_new.md"]
    assert res.calls == 1 + 12 and res.calls == len(calls)  # extract 1 + 前後比較(4×3)。規則帳が空なので重複判定はなし

    shared = (v / "memory" / "再発防止の規則.md").read_text(encoding="utf-8")
    assert "手で直さない" in shared and "1. 確認していない事実は断定しない" in shared
    assert "調べた結果" not in shared and "確認してから言って" not in shared  # 原因や引用は載せない
    mem = (v / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert mem.count("[[再発防止の規則]]") == 1 and mem.startswith("# 索引\n\n- 既存の1行\n")

    note = res.note_path.read_text(encoding="utf-8")
    body = [ln for ln in note.split("---\n", 2)[2].splitlines() if ln.startswith("- ")][:3]
    assert body[0].startswith("- 増えた規則:1件") and body[1].startswith("- 効いた規則:1件")
    assert body[2].startswith("- 効かなかった・判定不能の規則:0件")
    assert "再発した数" in note and "呼び出し" in note

    # 2回目: 新しい記録がないので、何も呼ばず、既存のノートも上書きしない。MEMORY.md も増えない
    calls.clear()
    res2 = weekly.run_weekly(cfg, _fake_ask(calls), NOW, log=lambda m: None)
    assert res2.calls == 0 and not res2.conversations and res2.note_path != res.note_path
    assert (v / "memory" / "MEMORY.md").read_text(encoding="utf-8").count("[[再発防止の規則]]") == 1


def test_weekly_call_limit_defers_check(tmp_path):
    v = _vault(tmp_path)
    cfg = weekly.load_config(tmp_path / "cfg.toml")
    cfg.max_calls = 5  # extract 1回はできるが、前後比較(12回)は足りない
    res = weekly.run_weekly(cfg, _fake_ask([]), NOW, log=lambda m: None)
    assert res.calls == 1 and len(res.unchecked) == 1 and not res.effective
    assert load_rules(v / "mist" / "rules.md")[0].status == weekly.UNCHECKED
    assert "再発防止の規則" in (v / "memory" / "MEMORY.md").read_text(encoding="utf-8")
    assert "効果が確かめられた規則は、まだありません" in (v / "memory" / "再発防止の規則.md").read_text(encoding="utf-8")

    # 次回: 上限に余裕があれば、後回しにした前後比較だけを行う(会話は読み直さない)
    cfg.max_calls = 300
    res2 = weekly.run_weekly(cfg, _fake_ask([]), NOW, log=lambda m: None)
    assert res2.calls == 12 and not res2.conversations
    assert load_rules(v / "mist" / "rules.md")[0].status == weekly.EFFECTIVE
    assert "確認していない事実は断定しない" in (v / "memory" / "再発防止の規則.md").read_text(encoding="utf-8")


def test_weekly_stops_after_repeated_failures(tmp_path):
    v = _vault(tmp_path)
    for i in range(4):
        (v / "conv" / f"2026-11-0{i + 1}_x{i}.md").write_text(ORIGINAL, encoding="utf-8")

    def broken(system, user, model, max_tokens=0, messages=None):
        raise weekly.llm.ClaudeCodeError("失敗")

    cfg = weekly.load_config(tmp_path / "cfg.toml")
    res = weekly.run_weekly(cfg, broken, NOW, log=lambda m: None)
    assert len(res.errors) == weekly.MAX_CONSECUTIVE_FAILURES and "続けて失敗" in res.stopped
    state = json.loads(cfg.state.read_text(encoding="utf-8"))
    assert state["last_run"] is None and state["processed"] == []  # 失敗した記録は次回やり直す


def test_ensure_memory_link_is_idempotent(tmp_path):
    m = tmp_path / "MEMORY.md"
    m.write_text("a", encoding="utf-8")
    assert weekly.ensure_memory_link(m) and not weekly.ensure_memory_link(m)
    assert m.read_text(encoding="utf-8").startswith("a\n- [[再発防止の規則]]")
    assert not weekly.ensure_memory_link(tmp_path / "なし.md")
