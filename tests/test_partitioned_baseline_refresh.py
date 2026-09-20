"""Focused schema-2 baseline refresh binding and resolver checks."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gah.adoption import AdoptionError
from gah import baseline_generations, run_evidence


class _Result:
    def __init__(self, value): self.value = value
    def fetchone(self): return self.value


class _DB:
    def __init__(self, row): self.row = row
    def execute(self, sql, params=()):
        if "FROM eval_runs" in sql:
            return _Result(self.row)
        raise AssertionError("unexpected database query")


class _PartitionedBook:
    def __init__(self, db, *, now, allowed_bindings, context_resolver):
        self.db, self.now = db, now
        self.run_id, self.digest = next(iter(allowed_bindings.items()))
        self.resolver = context_resolver
        self.wrong_connection = False

    def get_run(self, run_id):
        if self.wrong_connection:
            self.resolver(object(), self.now, {"manifest_ref": {"id": run_id}}, {})
        bound, profile, baseline = self.resolver(
            self.db, self.now, {"manifest_ref": {"id": run_id}}, {}
        )
        return {"execution_profile": profile, "bundle_digest": self.digest,
                "baseline_context": baseline}


class PartitionedBaselineRefreshTests(unittest.TestCase):
    def _fixture(self):
        run_id = "partitioned-refresh-run"
        baseline_context = {"baseline_ref": {"kind": "baseline", "id": "base", "digest": "a" * 64},
                            "targets": [], "contract": {"contract_id": "contract-v2"}}
        bound = {"manifest": {"schema_version": 2, "kind": "run_manifest", "run_id": run_id,
                              "purpose": "regression", "baseline_ref": baseline_context["baseline_ref"],
                              "created_at": 20, "use_cases": ["UC-LLM"], "control_ids": ["control"]},
                 "registry": {"controls": [{"control_id": "control"}]},
                 "contract": {"generation": 2},
                 "plan": {"schema_version": 1, "kind": "trial_plan", "plan_id": "partitioned-plan"}}
        source = {"bound": bound, "baseline_context": baseline_context,
                  "decision": {}, "evidences": [], "closure": {}, "evidence_states": {}}
        record = {"baseline_id": "next-baseline"}
        comparison = {"comparison_id": "next-comparison", "baseline_ref": bound["manifest"]["baseline_ref"]}
        candidate = {"record": record, "comparison_context": comparison}
        proposal = {"series_id": "series", "proposal_id": "proposal", "created_at": 20,
                    "expected_generation": 1, "record": record, "comparison_context": comparison,
                    "binding_digest": "binding-digest"}
        old = {"adopted_at": 10}
        row = {"run_id": run_id}
        prepared = {"bound_run": bound, "baseline_context": baseline_context,
                    "execution_profile": {"schema_version": 2, "kind": "execution_profile"}}
        return source, candidate, proposal, old, row, prepared

    def _run(self, *, wrong_connection=False):
        source, candidate, proposal, old, row, prepared = self._fixture()
        db = _DB(row)
        digest = "bundle-digest"
        book_ref = {}
        def book_factory(connection, **kwargs):
            book = _PartitionedBook(connection, **kwargs)
            book.wrong_connection = wrong_connection
            book_ref["book"] = book
            return book
        with patch.object(baseline_generations.base, "build_candidate", return_value=candidate), \
             patch.object(baseline_generations.base, "_trusted_binding", return_value="binding-digest") as trusted, \
             patch.object(baseline_generations, "predecessor", return_value=(old, {})), \
             patch("gah.regression_runs.for_run", return_value=prepared), \
             patch.object(run_evidence, "bound_bundle_digest", return_value=digest), \
             patch("gah.partitioned_normal_evidence.PartitionedNormalEvidenceBook", side_effect=book_factory), \
             patch.object(baseline_generations.base, "repeat_config_for_plan", return_value={"schema_version": 2}) as repeat, \
             patch.object(baseline_generations.base, "bind_baseline_record") as bind_record:
            result = baseline_generations.bind_candidate(db, proposal, source, 25)
        return result, trusted, repeat, bind_record, book_ref

    def test_partitioned_refresh_uses_fresh_partitioned_evidence_resolver(self):
        result, trusted, repeat, bind_record, books = self._run()
        self.assertEqual(result["record"], {"baseline_id": "next-baseline"})
        self.assertEqual(trusted.call_args.args[1], self._fixture()[0]["baseline_context"])
        self.assertEqual(repeat.call_args.kwargs["partitioned_context"], self._fixture()[0]["bound"])
        self.assertEqual(bind_record.call_args.kwargs["baseline_context"], self._fixture()[0]["baseline_context"])
        self.assertIsNotNone(books["book"])

    def test_partitioned_resolver_rejects_connection_or_time_drift(self):
        source, candidate, proposal, old, row, prepared = self._fixture()
        db = _DB(row)
        def book_factory(connection, **kwargs):
            book = _PartitionedBook(connection, **kwargs)
            book.wrong_connection = True
            return book
        with patch.object(baseline_generations.base, "build_candidate", return_value=candidate), \
             patch.object(baseline_generations.base, "_trusted_binding", return_value="binding-digest"), \
             patch.object(baseline_generations, "predecessor", return_value=(old, {})), \
             patch("gah.regression_runs.for_run", return_value=prepared), \
             patch.object(run_evidence, "bound_bundle_digest", return_value="bundle-digest"), \
             patch("gah.partitioned_normal_evidence.PartitionedNormalEvidenceBook", side_effect=book_factory):
            with self.assertRaisesRegex(AdoptionError, "^CURRENTNESS_UNAVAILABLE$"):
                baseline_generations.bind_candidate(db, proposal, source, 25)


if __name__ == "__main__":
    unittest.main()
