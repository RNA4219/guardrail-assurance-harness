"""明示v2→v4 migration CLIの外部境界を検査する。"""

import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from tests.legacy_adoption_fixture import create_legacy_adoption_fixture


ROOT = Path(__file__).resolve().parents[1]


class AdoptionMigrationCLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.fixture = self.folder / "v2-fixture.sqlite"
        create_legacy_adoption_fixture(self.fixture)

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, "-E", "-X", "utf8", "-m", "tools.migrate_evaluation_store", *args],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20,
        )

    def test_known_v2_migrates_once_and_is_not_ci_success(self):
        target = self.folder / "copy.sqlite"
        shutil.copyfile(self.fixture, target)
        first = self.cli(str(target))
        self.assertEqual(first.returncode, 0, first.stderr)
        result = json.loads(first.stdout)
        self.assertEqual(result["schema_version"], 4)
        self.assertFalse(result["ci_eligible"])
        second = self.cli(str(target))
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(second.stderr), {
            "schema_version": 1, "error": "UNSUPPORTED_STORE", "ci_eligible": False,
        })

    def test_missing_unknown_and_extra_arguments_are_fixed_errors_without_path(self):
        missing = self.folder / "missing-private-name.sqlite"
        result = self.cli(str(missing))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {
            "schema_version": 1, "error": "STORE_MISSING", "ci_eligible": False,
        })
        self.assertNotIn(str(missing), result.stderr)

        unknown = self.folder / "unknown.sqlite"
        db = sqlite3.connect(unknown)
        db.close()
        result = self.cli(str(unknown))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {
            "schema_version": 1, "error": "UNSUPPORTED_STORE", "ci_eligible": False,
        })

        result = self.cli(str(missing), "extra")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stderr), {
            "schema_version": 1, "error": "INVALID_ARGUMENTS", "ci_eligible": False,
        })
        self.assertNotIn("extra", result.stderr)


if __name__ == "__main__":
    unittest.main()
