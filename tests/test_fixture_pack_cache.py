"""fixture packの純粋materialization cache境界を検査する。"""
from copy import deepcopy
import hashlib
from pathlib import Path
import unittest
from unittest import mock

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah import evaluation_authority, fixture_admission, fixture_calibration
from gah import fixture_materialization, resources
from gah.immutable_cache import binding_cache


class FixturePackCacheTests(unittest.TestCase):
    def setUp(self):
        binding_cache.clear()
        self.calls = 0
        self.source_digest = "a" * 64
        self.old_builder = fixture_materialization.build_fixture_pack
        self.old_source_digest = evaluation_authority._source_digest

        def builder(policy, worker, lock, profile, created_at, run_id,
                    *, policy_generation):
            self.calls += 1
            return {
                "policy": policy,
                "worker": worker.hex(),
                "lock": lock,
                "profile": profile,
                "created_at": created_at,
                "run_id": run_id,
                "policy_generation": policy_generation,
            }

        fixture_materialization.build_fixture_pack = builder
        evaluation_authority._source_digest = lambda: self.source_digest
        self.builder = builder
        self.addCleanup(self._restore)

    def _restore(self):
        fixture_materialization.build_fixture_pack = self.old_builder
        evaluation_authority._source_digest = self.old_source_digest
        binding_cache.clear()

    def _base_args(self):
        return (
            {"policy_id": "policy-1", "controls": ["fixed-control"]},
            b"fixture-worker-v1",
            {"lock_id": "fixture-lock-v1", "version": 1},
            {
                "fixture_digest": "1" * 64,
                "adapter_digest": "2" * 64,
                "isolation_digest": "3" * 64,
            },
            100,
            "fixture-run-1",
            7,
        )

    def _pack(self, args=None):
        return fixture_admission._expected_fixture_pack(
            *(self._base_args() if args is None else args)
        )

    def test_hit_reconstructs_an_isolated_result(self):
        first = self._pack()
        expected = deepcopy(first)
        first["policy"]["controls"].append("mutated")
        first["lock"]["version"] = 99
        second = self._pack()
        self.assertEqual(self.calls, 1)
        self.assertEqual(second, expected)
        self.assertNotEqual(second, first)

    def test_each_binding_input_change_is_a_cache_miss(self):
        base = self._base_args()
        variants = []
        value = list(base)
        value[0] = {"policy_id": "policy-2", "controls": ["fixed-control"]}
        variants.append(tuple(value))
        value = list(base)
        value[1] = b"fixture-worker-v2"
        variants.append(tuple(value))
        value = list(base)
        value[2] = {"lock_id": "fixture-lock-v2", "version": 1}
        variants.append(tuple(value))
        value = list(base)
        value[3] = {**value[3], "adapter_digest": "4" * 64}
        variants.append(tuple(value))
        value = list(base)
        value[4] = 101
        variants.append(tuple(value))
        value = list(base)
        value[5] = "fixture-run-2"
        variants.append(tuple(value))
        value = list(base)
        value[6] = 8
        variants.append(tuple(value))
        self._pack(base)
        for variant in variants:
            self._pack(variant)
        self.source_digest = "b" * 64
        self._pack(base)
        self.assertEqual(self.calls, 9)

    def test_implementation_identity_is_part_of_the_cache_key(self):
        self._pack()
        old = fixture_materialization.build_fixture_pack

        def alternate(policy, worker, lock, profile, created_at, run_id,
                      *, policy_generation):
            self.calls += 1
            return {"alternate": True, "run_id": run_id}

        fixture_materialization.build_fixture_pack = alternate
        result = self._pack()
        self.assertTrue(result["alternate"])
        self.assertEqual(self.calls, 2)
        fixture_materialization.build_fixture_pack = old

    def test_non_plain_and_oversized_inputs_use_direct_path(self):
        class NonPlain:
            pass

        args = list(self._base_args())
        args[0] = {"policy_id": "policy-1", "opaque": NonPlain()}
        self._pack(tuple(args))
        self._pack(tuple(args))
        args = list(self._base_args())
        args[1] = bytearray(b"non-plain-worker")
        self._pack(tuple(args))
        self._pack(tuple(args))
        self.assertEqual(self.calls, 4)
        binding_cache.clear()
        self.calls = 0
        args = list(self._base_args())
        args[0] = {"policy_id": "large", "blob": "x" * fixture_admission._CACHE_PAYLOAD_BYTES}
        self._pack(tuple(args))
        self.assertEqual(self.calls, 1)
        self.assertEqual(binding_cache.info().currsize, 0)

    def test_verify_keeps_fresh_checks_on_cache_hit(self):
        policy = {"policy_id": "policy-verify"}
        worker = b"worker-verify"
        lock = {"lock_id": "lock-verify"}
        profile = {
            "fixture_digest": hashlib.sha256(worker).hexdigest(),
            "adapter_digest": "4" * 64,
            "isolation_digest": "5" * 64,
        }
        bound = {
            "manifest": {
                "run_id": "verify-run",
                "created_at": 10,
                "contract_ref": {"digest": "contract-digest"},
            },
            "contract": {"policy_generation": 2},
            "policy": policy,
        }
        prepared = {"bound_run": bound}
        materialization = {"manifest": {"manifest_id": "materialization"}}
        calibration = {
            "passed": True,
            "profile": {
                "worker_digest": profile["fixture_digest"],
                "normalizer_digest": profile["adapter_digest"],
            },
        }
        stored = {
            "prepared": prepared,
            "materialization": materialization,
            "calibration": calibration,
        }
        row = {
            "payload_json": "payload",
            "digest": "digest",
            "created_at": 10,
            "permission_generation": 2,
            "run_id": "verify-run",
            "contract_digest": "contract-digest",
        }
        counts = {"context": 0, "unpack": 0, "manifest": 0, "calibration": 0}

        def context():
            counts["context"] += 1
            return worker, lock, profile

        def unpack(_payload, _digest):
            counts["unpack"] += 1
            return stored

        def builder(policy_value, worker_value, lock_value, profile_value,
                    created_at, run_id, *, policy_generation):
            self.calls += 1
            self.assertEqual(policy_value, policy)
            self.assertEqual(worker_value, worker)
            self.assertEqual(lock_value, lock)
            self.assertEqual(profile_value, profile)
            self.assertEqual(created_at, 10)
            self.assertEqual(run_id, "verify-run")
            self.assertEqual(policy_generation, 2)
            return prepared

        def validate_manifest(*args, **kwargs):
            counts["manifest"] += 1
            return materialization

        def calibrate():
            counts["calibration"] += 1
            return calibration

        fixture_materialization.build_fixture_pack = builder
        with mock.patch.object(fixture_admission, "execution_context",
                               side_effect=context),              mock.patch.object(resources, "_unpack", side_effect=unpack),              mock.patch.object(fixture_materialization,
                               "validate_fixture_manifest",
                               side_effect=validate_manifest),              mock.patch.object(fixture_calibration,
                               "calibrate_fixed_fixture",
                               side_effect=calibrate):
            first = fixture_admission._verify(row, 20)
            second = fixture_admission._verify(row, 20)

        self.assertEqual(first, stored)
        self.assertEqual(second, stored)
        self.assertEqual(self.calls, 1)
        self.assertEqual(counts, {
            "context": 4, "unpack": 2, "manifest": 2, "calibration": 2,
        })


if __name__ == "__main__":
    unittest.main()
