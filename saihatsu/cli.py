"""コマンド saihatsu: extract / add / check / export。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import DEFAULT_MODEL, core, llm
from .rules import export_text, load_rules


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="saihatsu", description="人工知能の誤りを再発防止の規則に変え、効いたかを確かめる道具")
    p.add_argument("--model", default=DEFAULT_MODEL, help=f"使うモデル(既定: {DEFAULT_MODEL})")
    sub = p.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="会話記録から、苛立ちと直前の誤りを組で取り出す")
    e.add_argument("conversation")
    e.add_argument("-o", "--out", help="JSON の出力先(省略時は標準出力)")

    a = sub.add_parser("add", help="取り出し結果の規則を規則帳に足す(重複は発生回数+1)")
    a.add_argument("extraction")
    a.add_argument("--rules", default="rules.md")

    c = sub.add_parser("check", help="規則の前後比較(同じ誤りが再発するか)")
    c.add_argument("conversation")
    c.add_argument("--rules", default="rules.md")
    c.add_argument("--extraction", help="取り出し済みの JSON(省略時は extract をやり直す)")
    c.add_argument("-o", "--out", help="Markdown の表の出力先")
    c.add_argument("--repeat", type=int, default=1, help="同じ出し直しを繰り返す回数(再発率を見る)")

    x = sub.add_parser("export", help="規則帳を CLAUDE.md や会話の冒頭に貼れる形で出す")
    x.add_argument("--rules", default="rules.md")
    return p


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = _build_parser().parse_args(argv)
    try:
        if args.cmd == "extract":
            items = core.extract(_read(args.conversation), args.model)
            text = json.dumps(items, ensure_ascii=False, indent=2) + "\n"
            if args.out:
                Path(args.out).write_text(text, encoding="utf-8")
                print(f"{len(items)} 組を {args.out} に書きました。")
            else:
                print(text, end="")
        elif args.cmd == "add":
            items = json.loads(_read(args.extraction))
            for r in core.add(items, args.rules, args.model):
                tag = "新規" if r["created"] else f"重複(発生回数 {r['count']})"
                print(f"{r['id']} [{tag}] {r['rule']}")
        elif args.cmd == "check":
            items = json.loads(_read(args.extraction)) if args.extraction else None
            rows = core.check(_read(args.conversation), args.rules, args.model, items, repeat=args.repeat)
            table = core.render_check_table(rows)
            if args.out:
                Path(args.out).write_text(table, encoding="utf-8")
            print(table, end="")
        elif args.cmd == "export":
            print(export_text(load_rules(args.rules)), end="")
    except llm.ClaudeCodeError as ex:
        print(f"エラー: {ex}", file=sys.stderr)
        return 2
    except (FileNotFoundError, ValueError) as ex:
        print(f"エラー: {ex}", file=sys.stderr)
        return 1
    if llm.USAGE.calls:
        llm.report_usage()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
