"""既知のAdoptionStore v2を、明示指定したpathだけv3へ移行するCLI。"""

import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption_migrations import MigrationError, migrate_evaluation_store
from gah.wire import canonical_bytes


class _Parser(argparse.ArgumentParser):
    def error(self, _message):
        # 引数本文やpathをエラーへ混ぜない。
        raise MigrationError("INVALID_ARGUMENTS")


def _write_error(code: str) -> None:
    try:
        sys.stderr.write(json.dumps(
            {"schema_version": 1, "error": code, "ci_eligible": False},
            ensure_ascii=False, separators=(",", ":"),
        ) + "\n")
        sys.stderr.flush()
    except (OSError, ValueError):
        pass


def main(argv: list[str] | None = None) -> int:
    parser = _Parser(description="既知v2 AdoptionStoreの明示的v3移行")
    parser.add_argument("path")
    try:
        args = parser.parse_args(argv)
        result = migrate_evaluation_store(args.path)
        payload = canonical_bytes(result)
        sys.stdout.write(payload.decode("utf-8") + "\n")
        sys.stdout.flush()
        # 移行成功は診断結果であり、通常CIの合格を意味しない。
        return 0
    except MigrationError as error:
        _write_error(error.code)
    except (OSError, sqlite3.Error):
        _write_error("STORAGE_FAILURE")
    except KeyboardInterrupt:
        _write_error("INTERRUPTED")
    except Exception:
        _write_error("INTERNAL_ERROR")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
