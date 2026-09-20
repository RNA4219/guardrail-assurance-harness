from __future__ import annotations

import copy
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from gah.adoption import AdoptionError
from gah.partitioned_plan_store import _authority_context, create_schema, handle, validate_request
from gah.partitioned_trial_plan import partition_trial_plan
from gah.run_contracts import content_ref
from test_partitioned_trial_plan import _plan


class PartitionedPlanStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("BEGIN IMMEDIATE")
        create_schema(self.db)
        self.db.commit()
        self.contract = {
            "schema_version": 1, "kind": "evaluation_contract", "contract_id": "contract-1",
            "generation": 1,
        }
        plan = _plan()
        plan["contract_ref"] = content_ref("evaluation_contract", "contract-1", self.contract)
        self.index, self.segments = partition_trial_plan(plan)
        self.extension = type("Extension", (), {"digest": "a" * 64})()
        self.store = type("Store", (), {"_extension_digest": "a" * 64})()
        self.owner = ("manager", "manager-context")

    def tearDown(self) -> None:
        self.db.close()

    def _authority(self, *args, **kwargs):
        return copy.deepcopy(self.contract), 7, "a" * 64

    def _dispatch(self, action: str, request_id: str, *, now: int = 100, **fields):
        request = {"schema_version": 1, "action": action, "request_id": request_id, **fields}
        self.db.execute("BEGIN IMMEDIATE")
        try:
            with patch("gah.partitioned_plan_store._authority_context", side_effect=getattr(self, "authority_override", self._authority)):
                result = handle(self.extension, self.store, self.db, request, *self.owner, now)
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise
    def test_closed_request_shapes_and_schema_transaction_requirement(self) -> None:
        valid = {"schema_version": 1, "action": "plan_partition_status", "request_id": "req-1", "upload_id": "upload-1"}
        self.assertEqual(validate_request(valid), valid)
        with self.assertRaises(AdoptionError) as caught:
            validate_request({**valid, "unexpected": True})
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")
        db = sqlite3.connect(":memory:")
        with self.assertRaises(AdoptionError) as caught:
            create_schema(db)
        self.assertEqual(caught.exception.code, "TRANSACTION_REQUIRED")
        db.close()

    def test_upload_durable_resume_replay_and_fresh_reads(self) -> None:
        begin = self._dispatch("plan_partition_begin", "req-begin", upload_id="upload-1",
                               contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        self.assertEqual(begin["segment_count"], len(self.segments))
        first = self._dispatch("plan_partition_put_segment", "req-put-0", upload_id="upload-1", segment=self.segments[0])
        self.assertFalse(first["duplicate"])
        replay = self._dispatch("plan_partition_begin", "req-begin-replay", upload_id="upload-1",
                                contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        self.assertTrue(replay["resumed"])
        duplicate = self._dispatch("plan_partition_put_segment", "req-put-replay", upload_id="upload-1", segment=self.segments[0])
        self.assertTrue(duplicate["duplicate"])
        for number, segment in enumerate(self.segments[1:], start=1):
            self._dispatch("plan_partition_put_segment", f"req-put-{number}", upload_id="upload-1", segment=segment)
        status = self._dispatch("plan_partition_status", "req-status", upload_id="upload-1")
        self.assertEqual(status["missing_segments"], [])
        committed = self._dispatch("plan_partition_commit", "req-commit", upload_id="upload-1")
        self.db.commit()
        self.assertFalse(committed["ci_eligible"])
        index_ref = committed["plan_ref"]
        index_result = self._dispatch("plan_partition_read", "req-read-index", plan_ref=index_ref, segment_index=None)
        self.assertEqual(index_result["index"], self.index)
        segment_result = self._dispatch("plan_partition_read", "req-read-segment", plan_ref=index_ref, segment_index=0)
        self.assertEqual(segment_result["segment"], self.segments[0])
        self.assertEqual(segment_result["segment"], self.segments[0])

    def test_changed_duplicate_segment_rejected_and_owner_can_abort(self) -> None:
        self._dispatch("plan_partition_begin", "req-begin", upload_id="upload-2",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        segment = copy.deepcopy(self.segments[0])
        self._dispatch("plan_partition_put_segment", "req-put", upload_id="upload-2", segment=segment)
        changed = copy.deepcopy(segment)
        changed["entries"][0]["required"] = False
        with self.assertRaises(AdoptionError) as caught:
            self._dispatch("plan_partition_put_segment", "req-put2", upload_id="upload-2", segment=changed)
        self.assertIn(caught.exception.code, {"REFERENCE_MISMATCH", "SEGMENT_CONFLICT"})
        self.db.execute("BEGIN IMMEDIATE")
        with patch("gah.partitioned_plan_store._authority_context", side_effect=AssertionError("abort must allow stale cleanup")):
            result = handle(self.extension, self.store, self.db,
                            {"schema_version": 1, "action": "plan_partition_abort", "request_id": "req-abort", "upload_id": "upload-2"},
                            *self.owner, 999999)
        self.db.commit()
        self.assertTrue(result["aborted"])
        self.assertIsNone(self.db.execute("SELECT 1 FROM partition_plan_upload").fetchone())

    def test_expired_upload_cleanup_and_committed_collision_fail_closed(self) -> None:
        self._dispatch("plan_partition_begin", "req-exp", upload_id="upload-exp",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        self._dispatch("plan_partition_put_segment", "req-exp-put", upload_id="upload-exp", segment=self.segments[0])
        self._dispatch("plan_partition_begin", "req-new", now=4000, upload_id="upload-new",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        self.assertEqual(self.db.execute("SELECT upload_id FROM partition_plan_upload").fetchone()[0], "upload-new")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM partition_plan_segments").fetchone()[0], 0)
        upload = self.db.execute("SELECT * FROM partition_plan_upload").fetchone()
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("INSERT INTO partition_plan_commits VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (upload["plan_id"], upload["contract_series_id"], upload["contract_generation"],
                         upload["contract_ref_json"], upload["index_json"], upload["index_digest"],
                         upload["stored_bytes"], upload["owner_actor"], upload["owner_context"],
                         upload["permission_generation"], upload["extension_digest"], 4000))
        self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        with self.assertRaises(AdoptionError) as caught:
            handle(self.extension, self.store, self.db,
                   {"schema_version": 1, "action": "plan_partition_abort", "request_id": "req-abort-corrupt", "upload_id": "upload-new"},
                   *self.owner, 4000)
        self.db.rollback()
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.assertIsNotNone(self.db.execute("SELECT 1 FROM partition_plan_upload").fetchone())
    def test_current_generation_blocks_valid_old_history(self) -> None:
        self.db.execute("CREATE TABLE eval_current(series_id TEXT, generation INTEGER, payload_json TEXT, digest TEXT)")
        self.db.execute("CREATE TABLE eval_adoptions(series_id TEXT, generation INTEGER, payload_json TEXT, digest TEXT)")
        self.db.execute("INSERT INTO eval_current VALUES('series-1',2,'{}','x')")
        self.db.execute("INSERT INTO eval_adoptions VALUES('series-1',1,'{}','x')")
        self.db.execute("INSERT INTO eval_adoptions VALUES('series-1',2,'{}','x')")
        self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        with patch("gah.evaluation_authority._load_json", side_effect=lambda row, *_: {"contract_id": "contract-1", "generation": row["generation"]}), \
             patch("gah.partitioned_plan_store.validate_evaluation_contract", side_effect=lambda value: value), \
             patch("gah.evaluation_authority._validate_state") as validate_state:
            with self.assertRaises(AdoptionError) as caught:
                _authority_context(self.extension, self.store, self.db, "series-1", 1, 100)
        self.db.rollback()
        self.assertEqual(caught.exception.code, "CONTRACT_INVALID")
        validate_state.assert_not_called()

    def test_resume_rechecks_permission_and_source(self) -> None:
        self._dispatch("plan_partition_begin", "req-begin-stale", upload_id="upload-stale",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        for changed in ((8, "a" * 64), (7, "b" * 64)):
            self.db.execute("BEGIN IMMEDIATE")
            with patch("gah.partitioned_plan_store._authority_context", return_value=(self.contract, changed[0], changed[1])):
                with self.assertRaises(AdoptionError) as caught:
                    handle(self.extension, self.store, self.db,
                           {"schema_version": 1, "action": "plan_partition_begin", "request_id": "req-replay-stale",
                            "upload_id": "upload-stale", "contract_series_id": "series-1",
                            "expected_contract_generation": 1, "index": self.index},
                           *self.owner, 100)
            self.db.rollback()
            self.assertEqual(caught.exception.code, "UPLOAD_STALE")

    def test_missing_segment_commit_is_atomic_and_actual_bytes_gate(self) -> None:
        self._dispatch("plan_partition_begin", "req-begin-missing", upload_id="upload-missing",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        with self.assertRaises(AdoptionError) as caught:
            self._dispatch("plan_partition_commit", "req-commit-missing", upload_id="upload-missing")
        self.assertEqual(caught.exception.code, "SEGMENTS_INCOMPLETE")
        status = self._dispatch("plan_partition_status", "req-status-missing", upload_id="upload-missing")
        self.assertEqual(status["missing_segments"], [0])
        self._dispatch("plan_partition_abort", "req-abort-missing", upload_id="upload-missing")

        index_bytes = len(json.dumps(self.index, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        segment = self.segments[0]
        segment_bytes = len(json.dumps(segment, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        exact_total = index_bytes + segment_bytes
        self._dispatch("plan_partition_begin", "req-begin-cap", upload_id="upload-cap",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        with patch("gah.partitioned_plan_store.MAX_TOTAL_BYTES", exact_total - 1):
            with self.assertRaises(AdoptionError) as caught:
                self._dispatch("plan_partition_put_segment", "req-put-cap-fail", upload_id="upload-cap", segment=segment)
        self.assertEqual(caught.exception.code, "STORAGE_LIMIT")
        with patch("gah.partitioned_plan_store.MAX_TOTAL_BYTES", exact_total):
            self._dispatch("plan_partition_put_segment", "req-put-cap-ok", upload_id="upload-cap", segment=segment)
            result = self._dispatch("plan_partition_commit", "req-commit-cap", upload_id="upload-cap")
        self.assertEqual(result["entry_count"], 1)

    def test_committed_read_rejects_corrupt_row_and_shortcut_rechecks_source(self) -> None:
        self._dispatch("plan_partition_begin", "req-begin-readbad", upload_id="upload-readbad",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        self._dispatch("plan_partition_put_segment", "req-put-readbad", upload_id="upload-readbad", segment=self.segments[0])
        committed = self._dispatch("plan_partition_commit", "req-commit-readbad", upload_id="upload-readbad")
        self.db.execute("BEGIN IMMEDIATE")
        self.db.execute("UPDATE partition_plan_commits SET index_digest=? WHERE plan_id=?", ("0" * 64, self.index["plan_id"]))
        self.db.commit()
        with self.assertRaises(AdoptionError) as caught:
            self._dispatch("plan_partition_read", "req-read-corrupt", plan_ref=committed["plan_ref"], segment_index=None)
        self.assertEqual(caught.exception.code, "STORAGE_CORRUPT")
        self.authority_override = lambda *_a, **_k: (self.contract, 7, "b" * 64)
        try:
            with self.assertRaises(AdoptionError) as caught:
                self._dispatch("plan_partition_begin", "req-committed-shortcut", upload_id="upload-readbad",
                               contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        finally:
            del self.authority_override
        self.assertEqual(caught.exception.code, "PLAN_STALE")
    def test_wrong_owner_and_missing_transaction_rejected(self) -> None:
        self._dispatch("plan_partition_begin", "req-begin", upload_id="upload-3",
                       contract_series_id="series-1", expected_contract_generation=1, index=self.index)
        self.db.execute("BEGIN IMMEDIATE")
        with patch("gah.partitioned_plan_store._authority_context", side_effect=self._authority):
            with self.assertRaises(AdoptionError) as caught:
                handle(self.extension, self.store, self.db,
                       {"schema_version": 1, "action": "plan_partition_status", "request_id": "req-status", "upload_id": "upload-3"},
                       "validator", "validator-context", 100)
        self.db.rollback()
        self.assertEqual(caught.exception.code, "PERMISSION_DENIED")
        self.db.commit()
        with self.assertRaises(AdoptionError) as caught:
            handle(self.extension, self.store, self.db,
                   {"schema_version": 1, "action": "plan_partition_status", "request_id": "req-status", "upload_id": "upload-3"},
                   *self.owner, 100)
        self.assertEqual(caught.exception.code, "TRANSACTION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
