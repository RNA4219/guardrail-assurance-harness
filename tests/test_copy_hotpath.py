"""固定fixture既存行の返却で重複deepcopyしないことを確認する。"""
import copy
from pathlib import Path
import sys
import unittest
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests"))

from gah import cache_inputs, fixture_admission, fixture_materialization, run_contracts
from test_fixture_materialization import _pack
from test_run_contracts import _fixtures, _manifest, _plan


class _Result:
    def __init__(self, row):
        self.row = row

    def fetchone(self):
        return self.row


class _DB:
    in_transaction = True

    def __init__(self, row):
        self.row = row

    def execute(self, *_args):
        return _Result(self.row)


class CopyHotpathTests(unittest.TestCase):
    def test_manifest_binding_returns_validated_copies_without_recopying_them(self):
        fixtures = _fixtures()
        plan = _plan(fixtures)
        manifest = _manifest(fixtures, plan)
        original_deepcopy = run_contracts.deepcopy
        copied = []

        def counted(value, *args, **kwargs):
            copied.append(value)
            return original_deepcopy(value, *args, **kwargs)

        with mock.patch.object(run_contracts, "deepcopy", side_effect=counted):
            first = run_contracts.bind_run_manifest(
                manifest, fixtures["contract"], plan, fixtures["policy"],
                fixtures["registry"], fixtures["case_set"],
            )
            first_call_count = len(copied)
            second = run_contracts.bind_run_manifest(
                manifest, fixtures["contract"], plan, fixtures["policy"],
                fixtures["registry"], fixtures["case_set"],
            )

        # Manifest, contract and plan validate once; the private binder only
        # copies caller-owned target refs. Repeated binding stays independent.
        self.assertEqual(first_call_count, 4)
        self.assertEqual(len(copied), 8)
        self.assertIsNot(first["manifest"], manifest)
        self.assertIsNot(first["plan"], plan)
        self.assertIsNot(first["manifest"], second["manifest"])
        first["manifest"]["target_refs"][0]["id"] = "mutated-output"
        first["plan"]["entries"][0]["stage_ids"][0] = "mutated-stage"
        self.assertEqual(manifest["target_refs"][0]["id"], fixtures["target"]["id"])
        self.assertEqual(plan["entries"][0]["stage_ids"], ["stage-1"])
        self.assertEqual(second["manifest"]["target_refs"][0]["id"], fixtures["target"]["id"])
        self.assertEqual(second["plan"]["entries"][0]["stage_ids"], ["stage-1"])

    def test_trial_binding_keeps_only_copy_of_unvalidated_target_refs(self):
        fixtures = _fixtures()
        plan = _plan(fixtures)
        targets = [fixtures["target"]]
        original_deepcopy = run_contracts.deepcopy
        copied = []

        def counted(value, *args, **kwargs):
            copied.append(value)
            return original_deepcopy(value, *args, **kwargs)

        with mock.patch.object(run_contracts, "deepcopy", side_effect=counted):
            first = run_contracts.bind_trial_plan(
                plan, fixtures["contract"], fixtures["registry"],
                fixtures["case_set"], ["control-llm"], targets,
            )
            first_call_count = len(copied)
            second = run_contracts.bind_trial_plan(
                plan, fixtures["contract"], fixtures["registry"],
                fixtures["case_set"], ["control-llm"], targets,
            )

        self.assertEqual(first_call_count, 3)
        self.assertEqual(len(copied), 6)
        self.assertIsNot(first["plan"], plan)
        self.assertIsNot(first["target_refs"], targets)
        self.assertIsNot(first["plan"], second["plan"])
        first["plan"]["entries"][0]["stage_ids"][0] = "mutated-stage"
        first["target_refs"][0]["id"] = "mutated-target"
        self.assertEqual(plan["entries"][0]["stage_ids"], ["stage-1"])
        self.assertEqual(targets[0]["id"], fixtures["target"]["id"])
        self.assertEqual(second["plan"]["entries"][0]["stage_ids"], ["stage-1"])
        self.assertEqual(second["target_refs"][0]["id"], fixtures["target"]["id"])

    def test_core_bound_single_dict_contract_returns_fresh_trees(self):
        pack = _pack()
        cache_inputs.binding_cache.clear()
        self.addCleanup(cache_inputs.binding_cache.clear)

        first = fixture_materialization._core_bound(pack["bound_run"])
        second = fixture_materialization._core_bound(pack["bound_run"])

        self.assertIs(type(first), dict)
        self.assertEqual(set(first), {
            "manifest", "contract", "plan", "policy", "registry", "case_set",
            "selected_controls", "ci_eligible",
        })
        self.assertIsNot(first["manifest"], second["manifest"])
        first["manifest"]["target_refs"][0]["id"] = "mutated-core"
        self.assertNotEqual(second["manifest"]["target_refs"][0]["id"], "mutated-core")
        self.assertNotEqual(pack["bound_run"]["manifest"]["target_refs"][0]["id"], "mutated-core")

    def test_materialization_rebind_cache_keeps_both_public_results_isolated(self):
        pack = _pack()
        source = (REPO / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        args = {
            "bound_run": pack["bound_run"],
            "worker_source": source,
            "runtime_lock": pack["pack"]["runtime_lock"],
            "execution_profile": pack["pack"]["execution_profile"],
            "now": 100,
        }
        cache_inputs.binding_cache.clear()
        self.addCleanup(cache_inputs.binding_cache.clear)
        implementation = run_contracts.bind_run_manifest
        with mock.patch.object(run_contracts, "bind_run_manifest",
                               wraps=implementation) as rebound:
            materialized = fixture_materialization.materialize_fixture_manifest(**args)
            replayed = fixture_materialization.validate_fixture_manifest(
                materialized["manifest"], **args)

        # First call computes the fixed UC-CI binding; replay still rebuilds
        # and compares the manifest while its pure binding comes from cache.
        rebound.assert_called_once()
        self.assertEqual(materialized, replayed)
        replayed["manifest"]["records"][0]["scenario"] = "mutated-return"
        self.assertNotEqual(materialized["manifest"]["records"][0]["scenario"],
                            "mutated-return")
        self.assertEqual(args["bound_run"]["manifest"]["run_id"], "fixture-run-1")

    def test_existing_fixture_admission_returns_fresh_verified_value_without_copy(self):
        row = {"permission_generation": 4}
        values = []

        def verify(_row, _now):
            value = {"prepared": {"bound_run": {
                "policy": {"policy_id": "p"},
                "contract": {"policy_generation": 2},
            }}}
            values.append(value)
            return value

        with mock.patch.object(fixture_admission, "_verify", side_effect=verify), \
             mock.patch("copy.deepcopy", wraps=copy.deepcopy) as copied:
            first = fixture_admission.prepare(_DB(row), {"policy_id": "p"},
                                              2, "run", 100, 4)
            second = fixture_admission.prepare(_DB(row), {"policy_id": "p"},
                                               2, "run", 100, 4)

        self.assertIs(first, values[0])
        self.assertIs(second, values[1])
        self.assertIsNot(first, second)
        first["prepared"]["bound_run"]["policy"]["policy_id"] = "mutated"
        self.assertEqual(second["prepared"]["bound_run"]["policy"]["policy_id"], "p")
        copied.assert_not_called()


if __name__ == "__main__":
    unittest.main()
