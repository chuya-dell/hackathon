"""Claude API の薄い包み。鍵は環境変数 ANTHROPIC_API_KEY から読む。"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field

# 概算の単価(米ドル / 100万トークン)。仮置き。実際の請求と異なる場合がある。
_PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    by_model: dict = field(default_factory=dict)

    def add(self, model: str, i: int, o: int) -> None:
        self.input_tokens += i
        self.output_tokens += o
        self.calls += 1
        pin, pout = _PRICES["sonnet"]
        for k, v in _PRICES.items():
            if k in model:
                pin, pout = v
        self.cost_usd += i / 1e6 * pin + o / 1e6 * pout


USAGE = Usage()


class NoApiKey(RuntimeError):
    pass


def ask(system: str, user: str, model: str, max_tokens: int = 4000, messages: list | None = None) -> str:
    """Claude を1回呼ぶ。messages を渡すと、会話の続きとして出し直す。"""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise NoApiKey("環境変数 ANTHROPIC_API_KEY が設定されていません。")
    import anthropic

    client = anthropic.Anthropic()
    msgs = messages if messages is not None else [{"role": "user", "content": user}]
    kwargs = {"model": model, "max_tokens": max_tokens, "messages": msgs}
    if system:
        kwargs["system"] = system
    resp = client.messages.create(**kwargs)
    USAGE.add(model, resp.usage.input_tokens, resp.usage.output_tokens)
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")


def parse_json(text: str):
    """応答から JSON を取り出す。```json の囲みがあっても読む。"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        # 前後に説明文が付いた場合: 最初の [ か { から最後の ] か } まで
        starts = [i for i in (t.find("["), t.find("{")) if i >= 0]
        if not starts:
            raise
        s = min(starts)
        e = max(t.rfind("]"), t.rfind("}"))
        return json.loads(t[s : e + 1])


def report_usage(file=sys.stderr) -> None:
    print(
        f"[API] 呼び出し {USAGE.calls} 回 / 入力 {USAGE.input_tokens} / 出力 {USAGE.output_tokens} トークン"
        f" / 概算 約{USAGE.cost_usd:.3f}ドル(単価は仮置き)",
        file=file,
    )
