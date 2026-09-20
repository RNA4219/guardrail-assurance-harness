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

    def test_corpus_migration_requires_an_explicit_complete_option_pair(self):
        missing = self.folder / "uncreated-private-store.sqlite"
        for options in (("--partitioned-corpus",),
                        ("--expected-source-digest", "a" * 64)):
            with self.subTest(options=options):
                result = self.cli(str(missing), *options)
                self.assertEqual(result.returncode, 2)
                self.assertEqual(json.loads(result.stderr)["error"], "INVALID_ARGUMENTS")
                self.assertNotIn(str(missing), result.stderr)
                self.assertFalse(missing.exists())

    def test_corpus_migration_uses_the_pinned_v6_route_and_reopens_v7(self):
        from gah.adoption import AdoptionStore
        from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
        from gah.partitioned_corpus_authority import PartitionedCorpusEvaluationExtension
        target = self.folder / "v6-store.sqlite"
        store = AdoptionStore(target, extension=PartitionedRunEvaluationExtension())
        old_digest = store._extension_digest
        store.close()
        result = self.cli(str(target), "--partitioned-corpus",
                          "--expected-source-digest", old_digest)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 7)
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["ci_eligible"])
        reopened = AdoptionStore(target, extension=PartitionedCorpusEvaluationExtension())
        reopened.close()
        second = self.cli(str(target), "--partitioned-corpus",
                          "--expected-source-digest", old_digest)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(json.loads(second.stderr)["error"], "UNSUPPORTED_STORE")

    def test_corpus_route_does_not_migrate_another_version_or_wrong_pin(self):
        from gah.adoption import AdoptionStore
        from gah.partitioned_run_authority import PartitionedRunEvaluationExtension
        target = self.folder / "v6-pinned-store.sqlite"
        store = AdoptionStore(target, extension=PartitionedRunEvaluationExtension())
        store.close()
        for path in (target, self.fixture):
            with self.subTest(version=path.name):
                before = path.read_bytes()
                result = self.cli(str(path), "--partitioned-corpus",
                                  "--expected-source-digest", "a" * 64)
                self.assertEqual(result.returncode, 2)
                self.assertIn(json.loads(result.stderr)["error"],
                              {"CONFIG_MISMATCH", "UNSUPPORTED_STORE"})
                self.assertEqual(path.read_bytes(), before)
                self.assertNotIn(str(path), result.stderr)


if __name__ == "__main__":
    unittest.main()
