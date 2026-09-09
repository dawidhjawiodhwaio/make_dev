from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .core import MakeDevError, execute, load_config


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="make-dev", description="Ready IssueをCodexで実装しPRにします")
    sub = result.add_subparsers(dest="command")
    run = sub.add_parser("run", help="最古のReady Issueを1件処理します")
    run.add_argument("--repo", type=Path, default=Path.cwd(), help="対象Gitリポジトリ")
    run.add_argument("--dry-run", action="store_true", help="設定と実行予定を確認します")
    check = sub.add_parser("check", help="対象リポジトリの設定を検証します")
    check.add_argument("--repo", type=Path, default=Path.cwd())
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 0
    try:
        if args.command == "check":
            config = load_config(args.repo.resolve())
            print(f"設定OK: checks={len(config.test_commands)}")
        elif args.dry_run:
            config = load_config(args.repo.resolve())
            print(f"実行予定: repo={args.repo.resolve()} checks={len(config.test_commands)}")
        else:
            execute(args.repo.resolve())
        return 0
    except MakeDevError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
