"""通常CIの専用入口を任意extensionの成功宣言から分離する。"""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.regression_runs import validate_request


def ref(kind, identifier):
    return {"kind": kind, "id": identifier, "digest": "a" * 64}


class RegressionGateTests(unittest.TestCase):
    def setUp(self):
        self.request = {"schema_version": 1, "action": "ci_check", "request_id": "gate", "run_id": "run",
            "expected_manifest_ref": ref("run_manifest", "run"),
            "expected_contract_ref": ref("evaluation_contract", "contract"),
            "expected_baseline_ref": ref("baseline", "baseline"),
            "expected_target_refs": [ref("target", "target")], "expected_use_cases": ["UC-CI"]}

    def test_custom_extension_cannot_self_promote(self):
        class CustomExtension(EvaluationExtension):
            def execute(self, *args, **kwargs):
                raise AssertionError("custom CI execute must not be used")
        with tempfile.TemporaryDirectory() as folder:
            with AdoptionStore(Path(folder) / "db.sqlite", clock=lambda: 1000, extension=CustomExtension()) as store:
                with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                    store.dispatch(12004, 12004, self.request)

    def test_missing_run_fails_without_a_success_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            with AdoptionStore(Path(folder) / "db.sqlite", clock=lambda: 1000, extension=EvaluationExtension()) as store:
                result = store.dispatch(12004, 12004, self.request)
                self.assertEqual(result["exit_code"], 2)
                self.assertFalse(result["ci_eligible"])
                self.assertEqual(store._db.execute("SELECT COUNT(*) FROM authority_run_receipts").fetchone()[0], 0)

    def test_roles_and_shape_are_strict(self):
        with tempfile.TemporaryDirectory() as folder:
            with AdoptionStore(Path(folder) / "db.sqlite", clock=lambda: 1000, extension=EvaluationExtension()) as store:
                for uid in (12001, 12002, 12003):
                    with self.subTest(uid=uid), self.assertRaises(AdoptionError):
                        store.dispatch(uid, uid, self.request)
        for fields in ({"expected_use_cases": []}, {"expected_use_cases": ["UC-CI", "UC-CI"]},
            {"expected_target_refs": []}, {"expected_target_refs": [ref("control", "x")]},
            {"expected_baseline_ref": None}, {"ci_eligible": True}, {"schema_version": True}):
            with self.subTest(fields=fields), self.assertRaises(AdoptionError):
                validate_request({**deepcopy(self.request), **fields})
