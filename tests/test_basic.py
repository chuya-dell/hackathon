"""API を呼ばない部分の単体試験。Claude の呼び出しは偽物に差し替える。"""
import json

from saihatsu import core, llm
from saihatsu.conversation import AI, USER, parse_conversation, previous_user_turn
from saihatsu.rules import Rule, add_or_count, export_text, load_rules, save_rules

CONV = """前置きは無視される

## あなた
天気を教えて

## Claude
晴れです。

## あなた
長すぎる。もっと短く。

## Claude
はい。
複数行の
発言。
"""


def test_parse_conversation():
    turns = parse_conversation(CONV)
    assert [t.role for t in turns] == [USER, AI, USER, AI]
    assert turns[0].text == "天気を教えて"
    assert turns[3].text == "はい。\n複数行の\n発言。"
    assert previous_user_turn(turns, 3).index == 2


def test_parse_json_with_fence_and_prose():
    assert llm.parse_json('```json\n[{"a": 1}]\n```') == [{"a": 1}]
    assert llm.parse_json('結果は次です。\n{"x": true}\n以上') == {"x": True}


def test_rulebook_roundtrip(tmp_path):
    p = tmp_path / "rules.md"
    rules = []
    add_or_count(rules, Rule(id="", rule="結論を先に書く", error_type="冗長", cause="c", quote="q"), None)
    add_or_count(rules, Rule(id="", rule="略語は説明する"), None)
    save_rules(p, rules)
    loaded = load_rules(p)
    assert [r.id for r in loaded] == ["R001", "R002"]
    assert loaded[0].rule == "結論を先に書く" and loaded[0].error_type == "冗長"
    assert load_rules(tmp_path / "none.md") == []


def test_duplicate_increments_count():
    rules = []
    add_or_count(rules, Rule(id="", rule="結論を先に書く"), None)
    r, created = add_or_count(rules, Rule(id="", rule="先に結論を述べる"), "R001")
    assert not created and r.count == 2 and len(rules) == 1


def test_export_orders_by_count():
    rules = [Rule("R001", "A", count=1), Rule("R002", "B", count=3)]
    text = export_text(rules)
    assert text.index("- B") < text.index("- A")
    assert "規則はまだありません" in export_text([])


def fake_ask_factory(dup_id):
    def fake(system, user, model, max_tokens=0, messages=None):
        if system == core.EXTRACT_SYSTEM:
            return json.dumps([{"user_turn": 2, "ai_turn": 1, "error_type": "冗長",
                                "cause": "c", "rule": "3文以内で答える", "quote": "長すぎる"}], ensure_ascii=False)
        if system == core.DEDUP_SYSTEM:
            return json.dumps({"duplicate_of": dup_id})
        if system == core.JUDGE_SYSTEM:
            return json.dumps({"recurred": "規則あり" not in user and "RULE" not in user})
        return "応答(RULE)" if "守ること" in system else "応答"
    return fake


def test_extract_add_flow(tmp_path):
    ask = fake_ask_factory(None)
    items = core.extract(CONV, "m", ask)
    assert len(items) == 1 and items[0]["ai_turn"] == 1
    rp = tmp_path / "rules.md"
    log = core.add(items, rp, "m", ask)
    assert log[0]["created"] and log[0]["id"] == "R001"
    # 2回目: 重複と判定されれば行は増えず発生回数だけ増える
    ask2 = fake_ask_factory("R001")
    log = core.add(items, rp, "m", ask2)
    assert not log[0]["created"] and log[0]["count"] == 2
    assert len(load_rules(rp)) == 1


def test_extract_drops_invalid_turns():
    def ask(system, user, model, max_tokens=0, messages=None):
        return json.dumps([{"user_turn": 9, "ai_turn": 9, "rule": "x"},
                           {"user_turn": 0, "ai_turn": 0, "rule": "x"}])  # 範囲外と、AIでない番号
    assert core.extract(CONV, "m", ask) == []


def test_check_table(tmp_path):
    rp = tmp_path / "rules.md"
    save_rules(rp, [Rule("R001", "3文以内で答える")])
    ask = fake_ask_factory(None)
    rows = core.check(CONV, rp, "m", None, ask)
    assert len(rows) == 1
    table = core.render_check_table(rows)
    assert "| 誤りの種類 |" in table and "規則なし" in table
    # 規則なしの応答は再発、規則ありの応答は再発しない、と数えられる
    assert rows[0]["recurred_without"] is True and rows[0]["recurred_with"] is False
    assert "規則なし 1/1 → 規則あり 0/1" in table


def test_judge_nonbool_is_undetermined(tmp_path):
    rp = tmp_path / "rules.md"
    save_rules(rp, [Rule("R001", "x")])

    def ask(system, user, model, max_tokens=0, messages=None):
        if system == core.EXTRACT_SYSTEM:
            return json.dumps([{"user_turn": 2, "ai_turn": 1, "rule": "r"}])
        if system == core.JUDGE_SYSTEM:
            return '{"recurred": "false"}'  # 文字列は真偽値ではない
        return "応答"

    rows = core.check(CONV, rp, "m", None, ask)
    assert rows[0]["recurred_without"] is None
    assert "判定不能" in core.render_check_table(rows)
    assert "0/0" in core.render_check_table(rows)


def test_extract_requires_user_after_ai():
    def ask(system, user, model, max_tokens=0, messages=None):
        return json.dumps([{"user_turn": 0, "ai_turn": 1, "rule": "x"}])  # 利用者の発言が誤りより前
    assert core.extract(CONV, "m", ask) == []


def test_multiline_fields_do_not_break_rulebook(tmp_path):
    p = tmp_path / "rules.md"
    save_rules(p, [Rule("R001", "一行目\n## R999\n二行目", cause="a\nb")])
    loaded = load_rules(p)
    assert len(loaded) == 1 and "二行目" in loaded[0].rule
