"""別プロセス・実SQLite・実ファイルで部品CLIの契約を確認する。"""
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def request():
    return {
        "schema_version": 1, "request_id": "diagnostic-1",
        "purpose": "component_validation", "target_digest": "a" * 64,
        "contract_digest": "b" * 64, "observed_at": 100, "assessed_at": 101,
        "metrics": [{"metric_id": "recall", "name": "recall", "critical": False,
                     "numerator": 98, "denominator": 100,
                     "baseline": {"numerator": 100, "denominator": 100}}],
        "required_missing": False, "critical_missing": False, "integrity_failure": False,
        "forbidden_violation": False, "critical_violation": False, "warning": False,
    }


class CLITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.input = self.folder / "input.json"
        self.db = self.folder / "gah.sqlite"

    def cli(self, *args):
        env = dict(os.environ, PYTHONUTF8="1")
        return subprocess.run([sys.executable, "-E", "-X", "utf8", "-m", "tools.gah_cli", *args],
                              cwd=ROOT, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=15)

    def write(self, value=None):
        self.input.write_text(json.dumps(value if value is not None else request()), encoding="utf-8")

    def assess(self, *args):
        return self.cli("assess", "--input", str(self.input), "--db", str(self.db), *args)

    def test_round_trip_across_processes_never_claims_ci_success(self):
        self.write()
        first = self.assess()
        self.assertEqual(first.returncode, 1, first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual(report["assurance"], "HEALTHY")
        self.assertIs(report["ci_eligible"], False)
        restored = self.cli("show", "--id", "diagnostic-1", "--db", str(self.db))
        self.assertEqual(restored.returncode, 1, restored.stderr)
        self.assertEqual(json.loads(restored.stdout), report)

    def test_warning_and_hold_still_ineligible(self):
        for flag, state in [("warning", "WARNING"), ("critical_violation", "HOLD")]:
            with self.subTest(flag=flag):
                data = request(); data["request_id"] = flag; data[flag] = True
                self.write(data)
                result = self.assess()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(json.loads(result.stdout)["assurance"], state)
                self.assertIs(json.loads(result.stdout)["ci_eligible"], False)

    def test_same_id_cannot_replace_saved_diagnosis(self):
        self.write(); before = self.assess()
        self.assertEqual(before.returncode, 1, before.stderr)
        data = request(); data["metrics"][0]["numerator"] = 60
        self.write(data); conflict = self.assess()
        self.assertEqual(conflict.returncode, 2, conflict.stderr)
        saved = self.cli("show", "--id", "diagnostic-1", "--db", str(self.db))
        self.assertEqual(saved.stdout, before.stdout)

    def test_duplicate_keys_and_nonfinite_and_depth_are_rejected_without_echo(self):
        specimens = ['{"marker":"SYNTHETIC_PRIVATE","marker":0}', '{"n":NaN}', '[' * 30 + '0' + ']' * 30]
        for raw in specimens:
            with self.subTest(raw_type=len(raw)):
                self.input.write_text(raw, encoding="utf-8")
                result = self.assess()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("SYNTHETIC_PRIVATE", result.stderr)
                self.assertFalse(self.db.exists())

    def test_invalid_shape_does_not_create_database(self):
        variants = []
        for key, value in [("schema_version", True), ("purpose", "regression"), ("assessed_at", 1.5)]:
            bad = request(); bad[key] = value; variants.append(bad)
        unknown = request(); unknown["payload"] = "SYNTHETIC_PRIVATE"; variants.append(unknown)
        duplicate = request(); duplicate["metrics"] *= 2; variants.append(duplicate)
        ratio = request(); ratio["metrics"][0]["numerator"] = 101; variants.append(ratio)
        for data in variants:
            self.write(data); result = self.assess()
            self.assertEqual(result.returncode, 2, result.stdout)
            self.assertFalse(self.db.exists())
            self.assertNotIn("SYNTHETIC_PRIVATE", result.stderr)

    def test_oversized_input_rejected_before_database(self):
        self.input.write_bytes(b" " * 65537)
        self.assertEqual(self.assess().returncode, 2)
        self.assertFalse(self.db.exists())

    def test_output_conflict_keeps_existing_file_and_fails(self):
        self.write(); target = self.folder / "result.json"
        target.write_text("keep-existing", encoding="utf-8")
        result = self.assess("--output", str(target))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(target.read_text(encoding="utf-8"), "keep-existing")

    def test_database_open_failure_is_not_success(self):
        self.write()
        result = self.cli("assess", "--input", str(self.input), "--db", str(self.folder))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")

    def test_show_missing_database_has_no_side_effect(self):
        result = self.cli("show", "--id", "absent", "--db", str(self.db))
        self.assertEqual(result.returncode, 2)
        self.assertFalse(self.db.exists())

    def test_export_and_duplicate_delivery_are_identical(self):
        self.write(); target = self.folder / "result.json"
        first = self.assess("--output", str(target))
        again = self.assess()
        self.assertEqual(first.returncode, 1, first.stderr)
        self.assertEqual(again.returncode, 1, again.stderr)
        self.assertEqual(first.stdout, again.stdout)
        self.assertEqual(json.loads(target.read_text(encoding="utf-8")), json.loads(first.stdout))

    def test_changed_observation_with_same_result_is_not_duplicate(self):
        self.write(); first = self.assess()
        data = request(); data["observed_at"] += 1
        self.write(data); changed = self.assess()
        self.assertEqual(first.returncode, 1, first.stderr)
        self.assertEqual(changed.returncode, 2, changed.stderr)

    def test_corrupt_database_value_returns_fixed_error(self):
        self.write(); self.assertEqual(self.assess().returncode, 1)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("UPDATE reports SET canonical_json=?", (b"SYNTHETIC_PRIVATE",))
            db.commit()
        result = self.cli("show", "--id", "diagnostic-1", "--db", str(self.db))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("SYNTHETIC_PRIVATE", result.stderr)
        self.assertIs(json.loads(result.stderr)["ci_eligible"], False)

    def test_fsync_failure_keeps_failed_output_for_inspection(self):
        from gah.cli import main
        self.write(); target = self.folder / "result.json"

        def fail_after_replacement(fd):
            # 書込み中の同一pathを置換できる環境だけに依存させない。
            # fsync失敗後にもpathを自動削除しないことを確認する。
            raise OSError("synthetic fsync failure")

        with patch("gah.cli.os.fsync", side_effect=fail_after_replacement), patch("sys.stderr", new_callable=io.StringIO) as stderr:
            result = main(["assess", "--input", str(self.input), "--db", str(self.db), "--output", str(target)])
        self.assertEqual(result, 2)
        self.assertTrue(target.exists())
        self.assertEqual(json.loads(stderr.getvalue())["error"], "STORAGE_OR_OUTPUT_ERROR")

    def test_output_and_error_stream_failure_cannot_be_success(self):
        from gah.cli import main
        self.write()

        class Broken(io.StringIO):
            def write(self, text):
                raise OSError("synthetic stream failure")

        with patch("sys.stdout", Broken()), patch("sys.stderr", Broken()):
            result = main(["assess", "--input", str(self.input), "--db", str(self.db)])
        self.assertEqual(result, 2)


if __name__ == "__main__":
    unittest.main()
