import copy
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gah.adoption import AdoptionError
from gah.partitioned_case_set import INDEX_KIND as CASE_SET_INDEX_KIND
from gah.partitioned_corpus_authority import PartitionedCorpusEvaluationExtension
from gah.partitioned_corpus_store import (
    ACTIONS, FRESH_ACTIONS, TABLES, create_schema, handle, validate_request,
)
from gah.partitioned_scale_corpus import INDEX_KIND, partition_scale_corpus
from gah.query_scale_data import build_scale_corpus
from gah.run_contracts import content_ref


class PartitionedCorpusStoreTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.execute("BEGIN IMMEDIATE")
        create_schema(self.db)
        self.extension = PartitionedCorpusEvaluationExtension()
        self.store = SimpleNamespace(
            _extension=self.extension,
            _extension_digest=self.extension.digest,
            _db=self.db,
            _permission_generation=lambda db: 7,
        )
        self.policy = {"schema_version": 1, "kind": "policy_profile", "policy_id": "scale-policy"}
        self.policy_ref = content_ref("policy_profile", "scale-policy", self.policy)
        self.policy_patch = patch(
            "gah.evaluation_authority._policy",
            return_value=(self.policy, self.policy_ref, 3),
        )
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

    def tearDown(self):
        self.db.close()

    def call(self, body, *, actor="manager-1", context="manager-context", now=100):
        return handle(self.extension, self.store, self.db, body, actor, context, now)

    @staticmethod
    def request(action, request_id, **fields):
        return {"schema_version": 1, "action": action, "request_id": request_id, **fields}

    def make_parts(self, count=400):
        return partition_scale_corpus(build_scale_corpus(count))

    def begin(self, parts, upload_id="upload-scale-400"):
        index, case_index, _, _ = parts
        return self.call(self.request(
            "corpus_partition_begin", "request-begin", upload_id=upload_id,
            policy_series_id="scale-policy", expected_policy_generation=3,
            expected_permission_generation=7, index=index, case_set_index=case_index,
        ))

    def test_request_schema_is_closed_and_rejects_bool_counts(self):
        parts = self.make_parts()
        index, case_index, _, _ = parts
        valid = self.request(
            "corpus_partition_begin", "request-begin", upload_id="upload-scale-400",
            policy_series_id="scale-policy", expected_policy_generation=3,
            expected_permission_generation=7, index=index, case_set_index=case_index,
        )
        self.assertEqual(validate_request(valid), valid)
        extra = copy.deepcopy(valid)
        extra["unrecognized"] = True
        with self.assertRaises(AdoptionError) as caught:
            validate_request(extra)
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")
        boolean_count = copy.deepcopy(valid)
        boolean_count["index"]["case_count"] = True
        with self.assertRaises(AdoptionError) as caught:
            validate_request(boolean_count)
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")
        bad_read = self.request("corpus_partition_read", "read", policy_series_id="scale-policy",
            corpus_ref=content_ref(INDEX_KIND, index["corpus_id"], index), artifact_kind="corpus_index", segment_index=0)
        with self.assertRaises(AdoptionError) as caught:
            validate_request(bad_read)
        self.assertEqual(caught.exception.code, "INVALID_REQUEST")

    def test_fixed_400_corpus_upload_commit_read_and_ack_replay(self):
        index, case_index, case_parts, doc_parts = self.make_parts()
        begun = self.begin((index, case_index, case_parts, doc_parts))
        self.assertFalse(begun["resumed"])
        for i, segment in enumerate(case_parts):
            result = self.call(self.request("corpus_partition_put_case_set_segment", f"put-case-{i}",
                upload_id="upload-scale-400", segment=segment))
            self.assertFalse(result["duplicate"])
            replay = self.call(self.request("corpus_partition_put_case_set_segment", f"put-case-replay-{i}",
                upload_id="upload-scale-400", segment=segment))
            self.assertTrue(replay["duplicate"])
        for i, segment in enumerate(doc_parts):
            self.call(self.request("corpus_partition_put_document_segment", f"put-doc-{i}",
                upload_id="upload-scale-400", segment=segment))
        status = self.call(self.request("corpus_partition_status", "status", upload_id="upload-scale-400"))
        self.assertEqual(status["missing_case_set_segments"], [])
        self.assertEqual(status["missing_document_segments"], [])
        committed = self.call(self.request("corpus_partition_commit", "commit", upload_id="upload-scale-400"))
        self.assertFalse(committed["already_committed"])
        expected_case_set = build_scale_corpus(400)["case_set"]
        self.assertEqual(committed["case_set_ref"], content_ref("case_set", expected_case_set["case_set_id"], expected_case_set))
        self.db.commit()
        self.db.execute("BEGIN IMMEDIATE")
        replay = self.call(self.request("corpus_partition_commit", "commit-replay", upload_id="upload-scale-400"))
        self.assertTrue(replay["already_committed"])
        ref = committed["corpus_ref"]
        expected = {
            "corpus_index": index,
            "case_set_index": case_index,
            **{("case_set_segment", i): item for i, item in enumerate(case_parts)},
            **{("document_segment", i): item for i, item in enumerate(doc_parts)},
        }
        for key, artifact in expected.items():
            if isinstance(key, tuple):
                kind, ordinal = key
            else:
                kind, ordinal = key, None
            response = self.call(self.request("corpus_partition_read", f"read-{kind}-{ordinal}",
                policy_series_id="scale-policy", corpus_ref=ref, artifact_kind=kind, segment_index=ordinal))
            self.assertEqual(response["artifact"], artifact)
            self.assertFalse(response["ci_eligible"])
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM partition_scale_corpus_segments").fetchone()[0], 0)
        self.assertGreater(self.db.execute("SELECT COUNT(*) FROM partition_scale_corpus_committed_segments").fetchone()[0], 0)

    def test_segment_with_wrong_ordinal_binding_is_rejected_without_write(self):
        index, case_index, case_parts, doc_parts = self.make_parts()
        self.begin((index, case_index, case_parts, doc_parts))
        bad = copy.deepcopy(case_parts[0])
        bad["first_case_ordinal"] += 1
        with self.assertRaises(AdoptionError) as caught:
            self.call(self.request("corpus_partition_put_case_set_segment", "put-bad",
                upload_id="upload-scale-400", segment=bad))
        self.assertEqual(caught.exception.code, "SEGMENT_BINDING_MISMATCH")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM partition_scale_corpus_segments").fetchone()[0], 0)

    def test_begin_rejects_stale_permission_before_staging(self):
        self.store._permission_generation = lambda db: 8
        index, case_index, case_parts, doc_parts = self.make_parts()
        with self.assertRaises(AdoptionError) as caught:
            self.begin((index, case_index, case_parts, doc_parts))
        self.assertEqual(caught.exception.code, "PERMISSION_STALE")
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM partition_scale_corpus_upload").fetchone()[0], 0)

    def test_all_actions_are_fresh_and_diagnostic_only(self):
        self.assertEqual(set(ACTIONS), FRESH_ACTIONS)
        self.assertEqual(TABLES["partition_scale_corpus_commits"] & {"upload_id"}, {"upload_id"})


if __name__ == "__main__":
    unittest.main()
