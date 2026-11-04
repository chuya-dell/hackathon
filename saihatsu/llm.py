"""Claude の呼び出し。API ではなく、Claude Code(`claude -p`)を子のプロセスとして呼ぶ。

Claude Code にログインしていれば動く。API キーは使わない。子のプロセスからは
ANTHROPIC_API_KEY を外す(外さないと、プランの枠ではなく API の従量払いになる恐れがある)。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0
    seconds: float = 0.0
    cost_usd: float = 0.0  # Claude Code が報告する換算額(プラン利用では請求されない)

    def add(self, i: int, o: int, sec: float, cost: float) -> None:
        self.input_tokens += i
        self.output_tokens += o
        self.calls += 1
        self.seconds += sec
        self.cost_usd += cost


USAGE = Usage()
TIMEOUT_SEC = 300


class ClaudeCodeError(RuntimeError):
    pass


def child_env() -> dict:
    """子のプロセス用の環境変数。API キー類を外す。"""
    env = dict(os.environ)
    for k in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(k, None)
    return env


def _render_messages(messages: list) -> str:
    out = []
    for m in messages:
        who = "あなた" if m["role"] == "user" else "Claude"
        out.append(f"### {who}\n{m['content']}")
    return "\n\n".join(out)


def build_command(system: str, model: str) -> list[str]:
    exe = shutil.which("claude")
    if not exe:
        raise ClaudeCodeError("`claude` コマンドが見つかりません。Claude Code を入れてログインしてください。")
    cmd = [exe, "-p", "--model", model, "--output-format", "json", "--tools", "",
           "--no-session-persistence", "--disable-slash-commands"]
    if system:
        cmd += ["--system-prompt", system]
    return cmd


def run_claude(cmd: list[str], prompt: str) -> str:
    """`claude -p` を1回呼ぶ。この関数が唯一の呼び出し口(試験では差し替える)。"""
    t0 = time.time()
    with tempfile.TemporaryDirectory() as cwd:  # プロジェクトの設定を拾わない空のフォルダ
        try:
            p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                               env=child_env(), cwd=cwd, timeout=TIMEOUT_SEC)
        except subprocess.TimeoutExpired as ex:
            raise ClaudeCodeError(f"claude -p が {TIMEOUT_SEC} 秒で終わりませんでした。") from ex
    try:
        data = json.loads(p.stdout)
    except json.JSONDecodeError:
        raise ClaudeCodeError(f"claude -p の出力を読めません: {(p.stdout or p.stderr)[:300]}")
    if data.get("is_error") or p.returncode != 0:
        raise ClaudeCodeError(f"claude -p が失敗しました: {str(data.get('result', ''))[:300]}")
    u = data.get("usage", {})
    USAGE.add(
        int(u.get("input_tokens", 0)) + int(u.get("cache_read_input_tokens", 0)) + int(u.get("cache_creation_input_tokens", 0)),
        int(u.get("output_tokens", 0)), time.time() - t0, float(data.get("total_cost_usd") or 0),
    )
    return str(data.get("result", ""))


def ask(system: str, user: str, model: str, max_tokens: int = 0, messages: list | None = None) -> str:
    """Claude を1回呼ぶ。messages を渡すと、会話の続きとして出し直す。max_tokens は互換のため残す(使わない)。"""
    cmd = build_command(system, model)
    if messages is not None:
        prompt = (
            "以下は、あなた(Claude)と利用者の会話記録です。最後の「あなた」の発言に対する、"
            "Claude の次の返答だけを書いてください。見出しや前置きは付けません。\n\n" + _render_messages(messages)
        )
    else:
        prompt = user
    return run_claude(cmd, prompt)


def parse_json(text: str):
    """応答から JSON を取り出す。```json の囲みがあっても読む。"""
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        starts = [i for i in (t.find("["), t.find("{")) if i >= 0]
        if not starts:
            raise
        s = min(starts)
        e = max(t.rfind("]"), t.rfind("}"))
        return json.loads(t[s : e + 1])


def report_usage(file=sys.stderr) -> None:
    print(
        f"[Claude Code] 呼び出し {USAGE.calls} 回 / {USAGE.seconds:.0f} 秒 / 入力 {USAGE.input_tokens} / 出力 {USAGE.output_tokens} トークン"
        f" / 換算 約{USAGE.cost_usd:.3f}ドル(プラン利用では請求されない参考値)",
        file=file,
    )
