"""会話記録(md)の読み込み。「## あなた」「## Claude」の見出しで発言を区切る。"""
from __future__ import annotations

import re
from dataclasses import dataclass

USER = "user"
AI = "assistant"

# 「## あなた」「## Claude」。会話原本の「## あなた (2026-11-02 15:45:57)」のように、日時が付いてもよい
_HEADING = re.compile(r"^##\s*(あなた|Claude)\s*(\([^)]*\))?\s*$")
# 会話原本のツール呼び出し・結果の囲み(「> [!abstract]- ...」「> [!quote]- ...」)。本文から外す
_CALLOUT = re.compile(r"^>\s*\[![A-Za-z]+\]")


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
            body = re.sub(r"\n{3,}", "\n\n", "\n".join(buf)).strip()  # 囲みを外した跡の空行をつぶす
            if body:
                turns.append(Turn(len(turns), role, body))

    fence: str | None = None  # コードブロック内(``` や ~~~ の間)の見出しは境界にしない
    in_callout = False
    for line in text.splitlines():
        s = line.strip()
        if fence is None:
            # ツール呼び出しの囲みは、次の空行か、囲みでない行まで読み飛ばす
            if _CALLOUT.match(s):
                in_callout = True
                continue
            if in_callout:
                if s.startswith(">"):
                    continue
                in_callout = False
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


def clip_turns(turns: list[Turn], limit: int) -> list[Turn]:
    """長すぎる発言の中ほどを省く(1回の呼び出しに収めるため)。番号と役割は変えない。"""
    out = []
    for t in turns:
        text = t.text
        if len(text) > limit:
            head = int(limit * 0.7)
            text = text[:head] + "\n…(中略)…\n" + text[-(limit - head):]
        out.append(Turn(t.index, t.role, text))
    return out


def frontmatter_value(text: str, key: str) -> str | None:
    """先頭の `---` で囲まれた情報欄から、`key: 値` の値を返す(なければ None)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(rf"^{re.escape(key)}:\s*(.*)$", line)
        if m:
            return m.group(1).strip().strip('"')
    return None


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
