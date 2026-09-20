"""evidence_finalize reuses its digest-bound persisted aggregate."""
import hashlib
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from gah import run_evidence
from gah.assurance_authority import _aggregate_observed_at
from gah.adoption import AdoptionError
from gah.contracts import MAX_INTEGER
from gah.wire import canonical_bytes


class FinalizeAggregateReuseTests(unittest.TestCase):
    def test_finalize_reads_digest_bound_aggregate_without_reaggregation_and_replays(self):
        from tests.test_assurance_authority import AssuranceAuthorityTests, request
        harness = AssuranceAuthorityTests()
        harness.setUp()
        self.addCleanup(harness.doCleanups)
        with harness.open() as store:
            values, _, _ = harness._open_and_reserve(store)
            harness._settle_and_close(store, values)
            store.dispatch(12003, 12003, request(
                "evidence_record", "record-reuse", run_id="run-1", attempt=harness._attempt(values),
            ))
            aggregate_function = run_evidence.aggregation.aggregate
            with patch.object(run_evidence.aggregation, "aggregate", wraps=aggregate_function) as aggregate_call:
                with patch.object(run_evidence.RunEvidenceBook, "aggregate",
                                  side_effect=AssertionError("finalize must not reaggregate")):
                    first = store.dispatch(12004, 12004, request(
                        "evidence_finalize", "finalize-reuse", run_id="run-1",
                    ))
                    self.assertEqual(aggregate_call.call_count, 1)
                    evidence_row = store._db.execute(
                        "SELECT payload_json FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
                        (first["evidence_ref"]["kind"], first["evidence_ref"]["id"], first["evidence_ref"]["digest"]),
                    ).fetchone()
                    evidence = json.loads(evidence_row[0])
                    terminal = store._db.execute(
                        "SELECT terminal_json FROM terminals WHERE run_id=?", ("run-1",),
                    ).fetchone()
                    terminal_value = json.loads(terminal[0])
                    aggregate = store._db.execute(
                        "SELECT aggregate_json,aggregate_digest FROM aggregates WHERE run_id=? AND aggregate_digest=?",
                        ("run-1", terminal_value["decision"]["aggregate_digest"]),
                    ).fetchone()
                    self.assertIsNotNone(aggregate)
                    self.assertEqual(evidence["observed_at"], json.loads(aggregate[0])["observed_at"])
                    replay = store.dispatch(12004, 12004, request(
                        "evidence_finalize", "finalize-reuse-replay", run_id="run-1",
                    ))
            self.assertEqual(
                {k: v for k, v in first.items() if k not in {"action", "request_id"}},
                {k: v for k, v in replay.items() if k not in {"action", "request_id"}},
            )

    def test_receipt_and_evidence_match_legacy_aggregate_path_on_same_fixture(self):
        from tests.test_assurance_authority import AssuranceAuthorityTests, request
        from gah import assurance_authority

        def finalize_with_path(legacy):
            harness = AssuranceAuthorityTests()
            harness.setUp()
            self.addCleanup(harness.doCleanups)
            with harness.open() as store:
                values, _, _ = harness._open_and_reserve(store)
                harness._settle_and_close(store, values)
                store.dispatch(12003, 12003, request(
                    "evidence_record", "record-compat", run_id="run-1", attempt=harness._attempt(values),
                ))

                def legacy_reader(db, run_id, terminal):
                    row = db.execute("SELECT * FROM bound_runs WHERE run_id=?", (run_id,)).fetchone()
                    bound, profile, baseline = run_evidence.RunEvidenceBook._load_run(row)
                    binding = run_evidence.bound_bundle_digest(bound, baseline)
                    book = run_evidence.RunEvidenceBook(
                        db, now=harness.clock.value, allowed_bindings={run_id: binding},
                    )
                    return book.aggregate(run_id)["observed_at"]

                if legacy:
                    with patch.object(assurance_authority, "_aggregate_observed_at", side_effect=legacy_reader):
                        receipt = store.dispatch(12004, 12004, request(
                            "evidence_finalize", "finalize-compat", run_id="run-1",
                        ))
                else:
                    receipt = store.dispatch(12004, 12004, request(
                        "evidence_finalize", "finalize-compat", run_id="run-1",
                    ))
                evidence_row = store._db.execute(
                    "SELECT payload_json FROM authority_artifacts WHERE kind=? AND id=? AND digest=?",
                    (receipt["evidence_ref"]["kind"], receipt["evidence_ref"]["id"], receipt["evidence_ref"]["digest"]),
                ).fetchone()
                return ({k: v for k, v in receipt.items() if k not in {"action", "request_id"}},
                        json.loads(evidence_row[0]))

        new_receipt, new_evidence = finalize_with_path(False)
        legacy_receipt, legacy_evidence = finalize_with_path(True)
        self.assertEqual(new_receipt, legacy_receipt)
        self.assertEqual(new_evidence, legacy_evidence)


    def test_missing_wrong_run_bad_digest_and_invalid_time_are_rejected(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.execute("CREATE TABLE aggregates(run_id TEXT, aggregate_json TEXT, aggregate_digest TEXT)")
        terminal = {"decision": {"aggregate_digest": "a" * 64}}
        with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
            _aggregate_observed_at(db, "run-1", terminal)

        # A matching digest in another run is not a valid row for this run.
        raw = canonical_bytes({"observed_at": 5}).decode("utf-8")
        other_digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        db.execute("INSERT INTO aggregates VALUES(?,?,?)", ("run-other", raw, other_digest))
        with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
            _aggregate_observed_at(db, "run-1", {"decision": {"aggregate_digest": other_digest}})
        db.execute("DELETE FROM aggregates")

        # The row key matches terminal, but its payload digest does not.
        db.execute("INSERT INTO aggregates VALUES(?,?,?)", ("run-1", raw, "a" * 64))
        with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
            _aggregate_observed_at(db, "run-1", terminal)
        db.execute("DELETE FROM aggregates")

        for invalid in (True, -1, MAX_INTEGER + 1):
            with self.subTest(observed_at=invalid):
                payload = canonical_bytes({"observed_at": invalid}).decode("utf-8")
                payload_digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
                db.execute("INSERT INTO aggregates VALUES(?,?,?)",
                           ("run-1", payload, payload_digest))
                with self.assertRaisesRegex(AdoptionError, "^STORAGE_CORRUPT$"):
                    _aggregate_observed_at(db, "run-1", {"decision": {"aggregate_digest": payload_digest}})
                db.execute("DELETE FROM aggregates")

        # Matching content digest and run should be accepted.
        good = canonical_bytes({"observed_at": 5}).decode("utf-8")
        good_digest = hashlib.sha256(good.encode("utf-8")).hexdigest()
        accepted_terminal = {"decision": {"aggregate_digest": good_digest}}
        db.execute("INSERT INTO aggregates VALUES(?,?,?)", ("run-1", good, good_digest))
        self.assertEqual(_aggregate_observed_at(db, "run-1", accepted_terminal), 5)



if __name__ == "__main__":
    unittest.main()
