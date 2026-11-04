"""規則帳 rules.md の読み書き。1つの md ファイルに、重複なく規則を足していく。"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

HEADER = "# 規則帳\n\n人工知能の誤りから作った再発防止の規則。`saihatsu add` が追記する。\n"


@dataclass
class Rule:
    id: str
    rule: str
    error_type: str = ""
    cause: str = ""
    count: int = 1
    quote: str = ""


def load_rules(path: str | Path) -> list[Rule]:
    p = Path(path)
    if not p.exists():
        return []
    rules: list[Rule] = []
    cur: Rule | None = None
    for line in p.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^## (R\d+)\s*$", line)
        if m:
            cur = Rule(id=m.group(1), rule="")
            rules.append(cur)
            continue
        if cur is None:
            continue
        m = re.match(r"^- (規則|誤りの種類|原因|発生回数|根拠): ?(.*)$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if key == "規則":
            cur.rule = val
        elif key == "誤りの種類":
            cur.error_type = val
        elif key == "原因":
            cur.cause = val
        elif key == "発生回数":
            cur.count = int(val) if val.isdigit() else 1
        elif key == "根拠":
            cur.quote = val
    return rules


def save_rules(path: str | Path, rules: list[Rule]) -> None:
    out = [HEADER]
    for r in rules:
        quote = " ".join(r.quote.split())
        out.append(
            f"## {r.id}\n"
            f"- 規則: {r.rule}\n"
            f"- 誤りの種類: {r.error_type}\n"
            f"- 原因: {r.cause}\n"
            f"- 発生回数: {r.count}\n"
            f"- 根拠: {quote}\n"
        )
    Path(path).write_text("\n".join(out), encoding="utf-8")


def next_id(rules: list[Rule]) -> str:
    nums = [int(r.id[1:]) for r in rules if r.id[1:].isdigit()]
    return f"R{(max(nums) + 1) if nums else 1:03d}"


def add_or_count(rules: list[Rule], new: Rule, duplicate_of: str | None) -> tuple[Rule, bool]:
    """duplicate_of が既存の規則 ID なら発生回数を+1、そうでなければ新規追加。

    戻り値は (対象の規則, 新規追加したか)。
    """
    if duplicate_of:
        for r in rules:
            if r.id == duplicate_of:
                r.count += 1
                return r, False
    new.id = next_id(rules)
    new.count = 1
    rules.append(new)
    return new, True


def export_text(rules: list[Rule]) -> str:
    """CLAUDE.md や会話の冒頭に貼れる形。発生回数の多い順。"""
    if not rules:
        return "(規則はまだありません)\n"
    lines = ["# 守ること(過去の誤りから作った規則)", ""]
    for r in sorted(rules, key=lambda x: (-x.count, x.id)):
        lines.append(f"- {r.rule}")
    return "\n".join(lines) + "\n"
