"""GAHの部品診断CLI。CI合格の発行機能は接続されていない。"""
import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

from . import __version__
from .decision import assess
from .ledger import Ledger, LedgerError
from .wire import WireError, canonical_bytes, read_request


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        # 引数の本文/パスをログへ転記しない。
        raise WireError("INVALID_ARGUMENTS")


def _export(path: str, payload: bytes) -> None:
    """既存ファイルを変更せず出力する。不完全ファイルは完了記録にしない。"""
    target = Path(path)
    with target.open("xb") as stream:
        stream.write(payload + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    # 失敗したpathを削除しない。途中で別プロセスが置換した場合にも保全する。
    # 失敗時は終了2となる。残ったファイルを完了receiptとして扱わない。


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="GAH部品診断。通常CIの合格は発行しません。")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_Parser)
    assess_parser = sub.add_parser("assess", help="集計値を検査し、不変の部品診断を保存")
    assess_parser.add_argument("--input", required=True)
    assess_parser.add_argument("--db", required=True)
    assess_parser.add_argument("--output")
    for command, description in (
        ("show", "保存した部品診断を完全性確認して表示"),
        ("budget-show", "費用の精算状況を診断表示"),
        ("terminal-show", "保存した終了記録を完全性確認して表示"),
    ):
        read_parser = sub.add_parser(command, help=description)
        read_parser.add_argument("--id", required=True)
        read_parser.add_argument("--db", required=True)
    upgrade_parser = sub.add_parser("db-upgrade", help="既存のv1台帳を明示的にv2へ移行")
    upgrade_parser.add_argument("--db", required=True)
    try:
        args = parser.parse_args(argv)
        if args.command == "assess":
            report = assess(read_request(args.input))
            with Ledger(args.db) as ledger:
                ledger.store_report(report["request_id"], report)
                stored = ledger.get_report(report["request_id"])
            payload = canonical_bytes(stored)
            if args.output:
                _export(args.output, payload)
        else:
            # 診断・移行で存在しないDBを作らない。
            if not Path(args.db).is_file():
                raise WireError("DATABASE_NOT_FOUND")
            if args.command == "db-upgrade":
                stored = {**Ledger.migrate_v1(args.db), "purpose": "component_validation", "ci_eligible": False}
            else:
                with Ledger(args.db) as ledger:
                    if args.command == "show":
                        stored = ledger.get_report(args.id)
                    elif args.command == "budget-show":
                        stored = ledger.budget_closure(args.id)
                    else:
                        stored = ledger.get_terminal(args.id)
            payload = canonical_bytes(stored)
        sys.stdout.write(payload.decode("utf-8") + "\n")
        sys.stdout.flush()
        # 部品診断は成功に見えても通常CIの根拠を満たさない。
        return 1
    except (WireError, LedgerError) as error:
        code = error.code
    except ValueError:
        code = "INVALID_REQUEST"
    except (OSError, sqlite3.Error):
        code = "STORAGE_OR_OUTPUT_ERROR"
    except KeyboardInterrupt:
        # 中断しただけでは子処理停止/取消し記録の完了を証明できない。
        code = "INTERRUPTED_WITHOUT_RECEIPT"
    except Exception:
        # CLIの最終境界では予期しない障害もpayload/tracebackを出さず失敗へ閉じる。
        # 部品APIのテストでは元の例外を検出し、障害を黙って成功にしない。
        code = "INTERNAL_ERROR"
    try:
        sys.stderr.write(json.dumps({"schema_version": 1, "error": code, "ci_eligible": False}) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass
    return 2
