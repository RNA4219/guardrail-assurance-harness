from __future__ import annotations

from copy import deepcopy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.contracts import ContractError
from gah.fixture_materialization import (
    build_fixture_pack,
    materialize_fixture_manifest,
    validate_fixture_manifest,
)
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref


ROOT = Path(__file__).resolve().parents[1]


def _runtime(source: bytes) -> tuple[dict, dict]:
    digest = hashlib.sha256(source).hexdigest()
    lock = {
        "schema_version": 1,
        "image_id": "sha256:" + "1" * 64,
        "base_ref": "python@sha256:" + "2" * 64,
        "worker_digest": digest,
        "docker_binary_digest": "3" * 64,
        "entrypoint": ["/usr/local/bin/python", "-I", "-B", "/opt/gah/fixture_worker.py"],
        "environment": ["PYTHONHASHSEED=0"],
        "platform": "linux/amd64",
    }
    profile = {"fixture_digest": digest, "adapter_digest": "4" * 64, "isolation_digest": "5" * 64}
    return lock, profile


def _pack() -> dict:
    source = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
    lock, profile = _runtime(source)
    return build_fixture_pack(initial_policy_profile(), source, lock, profile, 100, "fixture-run-1")


class FixtureMaterializationTests(unittest.TestCase):
    def test_real_bound_pack_has_one_entry_per_fixed_scenario(self):
        pack = _pack()
        result = materialize_fixture_manifest(
            bound_run=pack["bound_run"],
            worker_source=(ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes(),
            runtime_lock=pack["pack"]["runtime_lock"],
            execution_profile=pack["pack"]["execution_profile"],
            now=100,
        )
        self.assertEqual(len(pack["pack"]["materials"]), 15)
        self.assertEqual(len(result["manifest"]["records"]), 15)
        self.assertEqual({r["scenario"] for r in result["manifest"]["records"]},
                         {f"constraint:C{n:02d}:good" for n in range(1, 11)} |
                         {f"mutation:F{n:02d}:healthy" for n in range(1, 6)})
        self.assertFalse(result["authority_connected"])
        self.assertFalse(result["ci_eligible"])

    def test_manifest_replay_is_deterministic(self):
        pack = _pack()
        source = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        args = {"bound_run": pack["bound_run"], "worker_source": source,
                "runtime_lock": pack["pack"]["runtime_lock"],
                "execution_profile": pack["pack"]["execution_profile"], "now": 100}
        result = materialize_fixture_manifest(**args)
        self.assertEqual(validate_fixture_manifest(result["manifest"], **args), result)

    def test_case_payload_target_and_stage_changes_are_rejected(self):
        pack = _pack()
        source = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        args = {"bound_run": pack["bound_run"], "worker_source": source,
                "runtime_lock": pack["pack"]["runtime_lock"],
                "execution_profile": pack["pack"]["execution_profile"], "now": 100}
        changed = deepcopy(pack["bound_run"])
        changed["case_set"]["cases"][0]["session_steps"][0]["input_ref"] = content_ref(
            "fixture_input", "changed", {"scenario": "constraint:C01:good"})
        args["bound_run"] = changed
        with self.assertRaises(ContractError):
            materialize_fixture_manifest(**args)

        changed = deepcopy(pack["bound_run"])
        changed["registry"]["controls"][0]["target_ref"] = content_ref("target", "wrong", {"x": 1})
        args["bound_run"] = changed
        with self.assertRaises(ContractError):
            materialize_fixture_manifest(**args)

        changed = deepcopy(pack["bound_run"])
        changed["fixture_pack"]["calibration_materials"][0]["input_payload"]["dimension"] = 99
        args["bound_run"] = changed
        with self.assertRaises(ContractError):
            materialize_fixture_manifest(**args)

    def test_transport_fixture_or_source_mismatch_is_rejected(self):
        pack = _pack()
        source = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        args = {"bound_run": pack["bound_run"], "worker_source": b"other",
                "runtime_lock": pack["pack"]["runtime_lock"],
                "execution_profile": pack["pack"]["execution_profile"], "now": 100}
        with self.assertRaises(ContractError):
            materialize_fixture_manifest(**args)
        self.assertNotEqual(source, b"other")

    def test_invalid_clock_and_policy_generation_use_fixed_errors(self):
        source = (ROOT / "fixtures" / "runtime" / "fixture_worker.py").read_bytes()
        lock, profile = _runtime(source)
        with self.assertRaises(ContractError):
            build_fixture_pack(initial_policy_profile(), source, lock, profile, "100", "fixture-run-1")
        with self.assertRaises(ContractError):
            build_fixture_pack(initial_policy_profile(), source, lock, profile, 100, "fixture-run-1", policy_generation=0)


if __name__ == "__main__":
    unittest.main()
