import copy
import hashlib
import json
import re
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.artifacts import ArtifactError, ArtifactStore, RETENTION_SECONDS
from gah.contracts import ContractError, decode_document
from gah.wire import canonical_bytes


def ref(kind="target", identifier="target-1"):
    return {"kind": kind, "id": identifier, "digest": "a" * 64}


def registry():
    return {"schema_version": 1, "kind": "control_registry", "registry_id": "registry-1", "controls": [
        {"control_id": "C01", "owner": "manager", "invariant": "合成成果物の構造を確認する",
         "criticality": "critical", "target_ref": ref(), "dependencies": [],
         "obligations": [{"obligation_id": "O01", "kind": "constraint", "required": True,
                           "event_policy": "forbidden", "evaluator_ref": ref("evaluator", "evaluator-1")}],
         "mutation_applicability": {"status": "not_applicable", "reason": "この登録では通常制約のみを検査"}}]}


class ArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "artifacts.sqlite"
        self.now = 1000
        self.document = registry()
        self.raw = canonical_bytes(self.document)
        self.digest = hashlib.sha256(self.raw).hexdigest()
        self.allowed = {("control_registry", "registry-1"): self.digest}

    def open(self, **kwargs):
        return ArtifactStore(self.path, allowed_artifacts=self.allowed, clock=lambda: self.now, **kwargs)

    def evidence(self, store, identifier="E01", **kwargs):
        artifact_ref = store.put("control_registry", "registry-1", self.raw)
        return store.record_evidence(identifier, artifact_ref=artifact_ref, subject_ref=ref(),
            conditions_ref=ref("policy", "policy-1"), producer_ref=ref("supervisor", "supervisor-1"),
            observed_at=1000, **kwargs)

    def test_restart_duplicate_and_clock_not_refreshed(self):
        with self.open() as store:
            original = self.evidence(store)
        self.now = 1100
        with self.open() as store:
            self.assertEqual(self.evidence(store), original)
            self.assertEqual(store.get(original["artifact_ref"]), self.document)
            history = store.read_evidence("E01")
            self.assertEqual(history["evidence"], original)
            self.assertEqual(history["use"]["valid_until"], 87400)
            self.assertFalse(history["use"]["ci_eligible"])

    def test_current_baseline_and_retention_boundaries(self):
        with self.open() as store:
            self.evidence(store)
            self.evidence(store, "E02", retention_until=1000 + 40)
            self.now += 40
            self.assertTrue(store.check_evidence("E02")["valid"])
            self.now += 1
            self.assertFalse(store.check_evidence("E02")["valid"])
            self.now = 1000 + 86400
            self.assertTrue(store.check_evidence("E01")["valid"])
            self.now += 1
            self.assertEqual(store.check_evidence("E01")["state"], "EXPIRED")
            self.now = 1000 + 30 * 86400
            self.assertTrue(store.check_evidence("E01", baseline=True)["valid"])
            self.now += 1
            self.assertFalse(store.check_evidence("E01", baseline=True)["valid"])

    def test_future_oversized_retention_and_clock_failure(self):
        with self.open() as store:
            artifact = store.put("control_registry", "registry-1", self.raw)
            for observed, retained in ((1001, None), (1000, 999), (1000, 1001 + RETENTION_SECONDS), (True, None)):
                with self.subTest(observed=observed, retained=retained), self.assertRaises(ContractError):
                    store.record_evidence("bad", artifact_ref=artifact, subject_ref=ref(), conditions_ref=ref(),
                        producer_ref=ref(), observed_at=observed, retention_until=retained)
            self.now = 999
            with self.assertRaisesRegex(ArtifactError, "^CLOCK_ROLLBACK$"):
                store.get(artifact)
            def broken_clock():
                raise RuntimeError("synthetic-clock-detail")
            store._clock = broken_clock
            with self.assertRaisesRegex(ArtifactError, "^CLOCK_UNAVAILABLE$"):
                store.get(artifact)

    def test_revoke_and_delete_impact_all_evidence_without_resurrection(self):
        with self.open() as store:
            first = self.evidence(store)
            self.evidence(store, "E02")
            generation = store.revoke_evidence("E01")
            self.assertEqual(store.revoke_evidence("E01"), generation)
            self.assertEqual(store.read_evidence("E01")["use"]["state"], "REVOKED")
            self.assertTrue(store.check_evidence("E02")["valid"])
            deleted = store.delete_artifact(first["artifact_ref"])
            self.assertGreater(deleted, generation)
            self.assertEqual(store.delete_artifact(first["artifact_ref"]), deleted)
            for identifier in ("E01", "E02"):
                self.assertEqual(store.read_evidence(identifier)["use"]["state"], "DELETED")
            with self.assertRaisesRegex(ArtifactError, "^ARTIFACT_DELETED$"):
                store.put("control_registry", "registry-1", self.raw)
        with self.open() as store:
            self.assertEqual(store.check_evidence("E02")["state"], "DELETED")
            self.assertEqual(store._db.execute("SELECT payload FROM artifacts").fetchone()[0], None)

    def test_disallowed_content_never_enters_storage(self):
        marker = "SYNTHETIC_FORBIDDEN_MARKER"
        bad = copy.deepcopy(self.document)
        bad["controls"][0]["invariant"] = marker
        with self.open() as store:
            with self.assertRaisesRegex(ArtifactError, "^DATA_NOT_ADMITTED$"):
                store.put("control_registry", "registry-1", canonical_bytes(bad))
            self.assertEqual(store._db.execute("SELECT count(*) FROM artifacts").fetchone()[0], 0)
            self.assertEqual(store._db.execute("SELECT count(*) FROM evidence").fetchone()[0], 0)
        self.assertNotIn(marker.encode(), self.path.read_bytes())

    def test_artifact_and_evidence_conflicting_redelivery_rejected(self):
        with self.open() as store:
            original = self.evidence(store)
            with self.assertRaisesRegex(ArtifactError, "^EVIDENCE_CONFLICT$"):
                self.evidence(store, retention_until=original["retention_until"] - 1)
            altered = copy.deepcopy(self.document)
            altered["controls"][0]["invariant"] = "別の合成条件"
            raw = canonical_bytes(altered)
            store._allowed[("control_registry", "registry-1")] = hashlib.sha256(raw).hexdigest()
            with self.assertRaisesRegex(ArtifactError, "^ARTIFACT_CONFLICT$"):
                store.put("control_registry", "registry-1", raw)
            self.assertEqual(store.get(original["artifact_ref"]), self.document)

    def test_delete_rolls_back_tombstone_generation_and_content_on_failure(self):
        with self.open() as store:
            original = self.evidence(store)
            def deny_delete(action, arg1, arg2, *_):
                return sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_UPDATE and arg1 == "artifacts" else sqlite3.SQLITE_OK
            store._db.set_authorizer(deny_delete)
            with self.assertRaisesRegex(ArtifactError, "^STORAGE_FAILURE$"):
                store.delete_artifact(original["artifact_ref"])
            store._db.set_authorizer(None)
            self.assertEqual(store.get(original["artifact_ref"]), self.document)
            self.assertEqual(store.check_evidence("E01")["revocation_generation"], 0)
            self.assertEqual(store._db.execute("SELECT count(*) FROM revocations").fetchone()[0], 0)

    def test_corrupt_artifact_and_cross_record_evidence_are_rejected(self):
        with self.open() as store:
            first = self.evidence(store)
            self.evidence(store, "E02")
            second = store._db.execute("SELECT payload,digest FROM evidence WHERE id='E02'").fetchone()
            store._db.execute("UPDATE evidence SET payload=?,digest=? WHERE id='E01'", tuple(second))
            with self.assertRaisesRegex(ArtifactError, "^STORAGE_CORRUPT$"):
                store.check_evidence("E01")
            self.assertTrue(store.check_evidence("E02")["valid"])
            store._db.execute("UPDATE artifacts SET payload=?", (b'{}',))
            with self.assertRaisesRegex(ArtifactError, "^STORAGE_CORRUPT$"):
                store.get(first["artifact_ref"])

    def test_concurrent_duplicate_and_revocation_share_single_generation(self):
        with self.open() as store:
            self.evidence(store)
        barrier = threading.Barrier(2)
        def revoke():
            with self.open() as store:
                barrier.wait(timeout=5)
                self.evidence(store)
                return store.revoke_evidence("E01")
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(revoke) for _ in range(2)]
            self.assertEqual([future.result(timeout=10) for future in futures], [1, 1])
        with self.open() as store:
            self.assertEqual(store._db.execute("SELECT count(*) FROM evidence").fetchone()[0], 1)
            self.assertFalse(store.check_evidence("E01")["valid"])

    def test_storage_initialization_failure_and_foreign_database(self):
        with self.assertRaisesRegex(ArtifactError, "^STORAGE_FAILURE$"):
            ArtifactStore(self.path / "absent" / "state.sqlite", allowed_artifacts={})
        db = sqlite3.connect(self.path)
        db.execute("CREATE TABLE foreign_table(id INTEGER)")
        db.close()
        with self.assertRaisesRegex(ArtifactError, "^UNSUPPORTED_STORE$"):
            self.open()
        db = sqlite3.connect(self.path)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 0)
        db.close()

    def test_case_set_storage_and_admission_policy_snapshot(self):
        from tests.test_corpus import case, case_set
        document = case_set(case())
        raw = canonical_bytes(document)
        digest = hashlib.sha256(raw).hexdigest()
        self.allowed[("case_set", "set-1")] = digest
        with self.open() as store:
            artifact = store.put("case_set", "set-1", raw)
            self.assertEqual(store.get(artifact), document)
            original = dict(self.allowed)
            bad = copy.deepcopy(self.document)
            bad["controls"][0]["invariant"] = "許可表の外からの変更"
            payload = canonical_bytes(bad)
            self.allowed[("control_registry", "registry-1")] = hashlib.sha256(payload).hexdigest()
            with self.assertRaisesRegex(ArtifactError, "^DATA_NOT_ADMITTED$"):
                store.put("control_registry", "registry-1", payload)
            self.allowed = original

    def test_keyboard_interrupt_rolls_back(self):
        with self.open() as store:
            with self.assertRaises(KeyboardInterrupt):
                with store._transaction() as db:
                    db.execute("UPDATE artifact_meta SET value=99 WHERE key='generation'")
                    raise KeyboardInterrupt()
            self.assertEqual(store._db.execute("SELECT value FROM artifact_meta WHERE key='generation'").fetchone()[0], 0)


class ContractDecoderTests(unittest.TestCase):
    def test_schema_reference_patterns_reject_trailing_newlines(self):
        root = Path(__file__).resolve().parents[1]
        for name in ("control-registry", "case-set"):
            schema = json.loads((root / "schemas" / (name + ".v1.schema.json")).read_text(encoding="utf-8"))
            patterns = []
            pending = [schema]
            while pending:
                value = pending.pop()
                if type(value) is dict:
                    if "pattern" in value:
                        patterns.append(value["pattern"])
                    pending.extend(value.values())
                elif type(value) is list:
                    pending.extend(value)
            for pattern in patterns:
                if "A-Za-z0-9" in pattern or "0-9a-f" in pattern:
                    valid = "a" * 64 if "0-9a-f" in pattern else "example-id"
                    self.assertIsNotNone(re.search(pattern, valid))
                    self.assertIsNone(re.search(pattern, valid + "\n"))

    def test_other_set_iterator_is_bounded_before_collection(self):
        from gah.corpus import corpus_report
        from tests.test_corpus import case, case_set
        calls = []
        def oversized_stream():
            for index in range(1000):
                calls.append(index)
                if index > 64:
                    raise AssertionError("iterable consumed beyond bound")
                yield case_set(case())
        with self.assertRaises(ContractError):
            corpus_report(case_set(case()), oversized_stream())
        self.assertEqual(len(calls), 65)

    def test_rejects_duplicates_numbers_depth_size_and_unicode(self):
        bad = [b'{"a":1,"a":2}', b'{"a":1.0}', b'{"a":NaN}', b'[]', b'{"a":9007199254740992}',
               b'{' + b' ' * 1048576 + b'}', b'{"a":' + b'[' * 17 + b'0' + b']' * 17 + b'}',
               b'{"a":"\\ud800"}', b'{"a":-9007199254740992}']
        for raw in bad:
            with self.subTest(length=len(raw)), self.assertRaises(ContractError) as caught:
                decode_document(raw)
            self.assertNotIn("9007199254740992", str(caught.exception))
        self.assertEqual(decode_document(b'{"a":true,"b":9007199254740991}')["a"], True)


if __name__ == "__main__":
    unittest.main()
