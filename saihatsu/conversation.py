"""会話記録(md)の読み込み。「## あなた」「## Claude」の見出しで発言を区切る。"""
from __future__ import annotations

import re
from dataclasses import dataclass

USER = "user"
AI = "assistant"

_HEADING = re.compile(r"^##\s*(あなた|Claude)\s*$")


@dataclass
class Turn:
    index: int
    role: str  # USER か AI
    text: str


def parse_conversation(text: str) -> list[Turn]:
    """見出しごとに発言を切り出す。見出しより前の文章は無視する。"""
    turns: list[Turn] = []
    role: str | None = None
    buf: list[str] = []

    def flush() -> None:
        if role is not None:
            body = "\n".join(buf).strip()
            if body:
                turns.append(Turn(len(turns), role, body))

    fence: str | None = None  # コードブロック内(``` や ~~~ の間)の見出しは境界にしない
    for line in text.splitlines():
        s = line.strip()
        if fence is None:
            if s.startswith("```") or s.startswith("~~~"):
                fence = s[:3]
        elif s.startswith(fence):
            fence = None
        m = None if fence is not None else _HEADING.match(s)
        if fence is not None and role is not None:
            buf.append(line)
            continue
        if fence is None and s.startswith(("```", "~~~")) and role is not None:
            buf.append(line)  # 閉じ側の囲み行
            continue
        if m:
            flush()
            role = USER if m.group(1) == "あなた" else AI
            buf = []
        elif role is not None:
            buf.append(line)
    flush()
    return turns


def render_turns(turns: list[Turn]) -> str:
    """番号つきで人工知能に見せる形に直す。"""
    out = []
    for t in turns:
        who = "あなた" if t.role == USER else "Claude"
        out.append(f"### [{t.index}] {who}\n{t.text}")
    return "\n\n".join(out)


def previous_user_turn(turns: list[Turn], ai_index: int) -> Turn | None:
    """指定した人工知能の発言の直前にある、利用者の発言を返す。"""
    for t in reversed(turns[:ai_index]):
        if t.role == USER:
            return t
    return None
