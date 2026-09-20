"""Real SQLite/AdoptionStore v7 integration; never starts Docker."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from gah.adoption import AdoptionError, AdoptionStore
from gah.evaluation_authority import EvaluationExtension
from gah.partitioned_authority import PartitionedEvaluationExtension
from gah.partitioned_run_authority import PartitionedRunEvaluationExtension, _exact_source_digest
from gah.partitioned_corpus_authority import PartitionedCorpusEvaluationExtension
from gah import partitioned_corpus_authority as authority
from gah import partitioned_corpus_store as storage
from gah.policy import initial_policy_profile
from tests.test_fixture_admission import Clock, request


class PartitionedCorpusAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "authority.sqlite"
        self.clock = Clock()
        self.policy = initial_policy_profile()

    def open(self, extension=None):
        return AdoptionStore(self.path, clock=self.clock, bootstrap_policy=self.policy,
                             extension=extension or PartitionedCorpusEvaluationExtension())

    def call(self, store, uid, action, request_id, **fields):
        return store.dispatch(uid, uid, request(action, request_id, **fields))

    def seed_policy(self, store):
        self.call(store, 12001, "propose", "seed-propose", proposal_id="policy-proposal",
                  series_id=self.policy["policy_id"], expected_generation=0, policy=self.policy)
        self.call(store, 12003, "validate", "seed-validate", proposal_id="policy-proposal",
                  validation_id="policy-validation")
        self.call(store, 12001, "adopt", "seed-adopt", proposal_id="policy-proposal",
                  validation_id="policy-validation", expected_generation=0)

    def test_v7_new_database_does_not_implicitly_migrate_prior_schemas(self):
        for cls, version in ((EvaluationExtension, 4), (PartitionedEvaluationExtension, 5),
                             (PartitionedRunEvaluationExtension, 6)):
            with self.subTest(version=version):
                self.path = Path(self.temp.name) / (str(version) + ".sqlite")
                with self.open(cls()) as store:
                    self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], version)
                    names = {r[0] for r in store._db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    self.assertFalse(set(storage.TABLES) & names)
                with self.assertRaises(AdoptionError):
                    self.open()
                with self.open(cls()) as store:
                    self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], version)
        self.path = Path(self.temp.name) / "v7.sqlite"
        with self.open() as store:
            self.assertEqual(store._db.execute("PRAGMA user_version").fetchone()[0], 7)
            self.assertTrue(set(storage.ACTIONS) <= store._fresh_actions)
        with self.assertRaises(AdoptionError):
            self.open(PartitionedRunEvaluationExtension())

    def test_subclasses_cannot_use_v7_authority_even_with_matching_metadata(self):
        class Derived(PartitionedCorpusEvaluationExtension):
            pass
        class DerivedSix(PartitionedRunEvaluationExtension):
            pass
        for cls in (Derived, DerivedSix):
            with self.subTest(cls=cls.__name__), self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                self.open(cls())
            with self.subTest(dispatcher=cls.__name__), self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                _exact_source_digest(cls())
        self.assertFalse(self.path.exists())

    def test_inherited_generation_one_diagnostic_remains_non_ci(self):
        from gah.assurance_authority import fixed_profile
        from tests.test_partitioned_authority import PartitionedAuthorityTests
        helper = PartitionedAuthorityTests("test_real_adoption_upload_restart_replay_commit_and_fresh_read")
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        with helper.open(PartitionedCorpusEvaluationExtension()) as store:
            helper.seed(store)
            helper.begin(store)
            helper.put(store)
            self.call(store, 12001, "plan_partition_commit", "v7-plan-commit", upload_id="upload")
            manifest = deepcopy(helper.bound["manifest"])
            manifest.update(schema_version=2, run_id="v7-diagnostic", purpose="diagnostic",
                            baseline_ref=None, plan_ref=deepcopy(helper.plan_ref))
            value = request("run_begin_v2", "v7-run-begin", manifest=manifest,
                contract_series_id="partition-series", expected_generation=1, execution_profile=fixed_profile())
            begun = store.dispatch(12004, 12004, value)
            self.assertFalse(begun["ci_eligible"])
            self.assertFalse(begun["admission_verified"])
            self.assertTrue(store.dispatch(12004, 12004, value)["duplicate"])
            status = self.call(store, 12004, "run_status_v2", "v7-status", run_id="v7-diagnostic")
            self.assertFalse(status["ci_eligible"])
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM eval_runs_v2").fetchone()[0], 1)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM authority_run_receipts").fetchone()[0], 0)
            with self.assertRaisesRegex(AdoptionError, "^SCOPE_UNSUPPORTED$"):
                store.dispatch(12004, 12004, {**value, "request_id": "gen2-rejected", "expected_generation": 2})

    @classmethod
    def setUpClass(cls):
        from gah.partitioned_scale_corpus import partition_scale_corpus
        from gah.query_scale_data import build_scale_corpus
        cls.corpora = {n: build_scale_corpus(n) for n in (400, 800, 1600)}
        cls.parts = {n: partition_scale_corpus(value) for n, value in cls.corpora.items()}

    def corpus_begin(self, n=400, upload_id="corpus-upload"):
        index, case_index, _, _ = self.parts[n]
        return request("corpus_partition_begin", upload_id + "-begin", upload_id=upload_id,
                       policy_series_id=self.policy["policy_id"], expected_policy_generation=1,
                       expected_permission_generation=0, index=deepcopy(index), case_set_index=deepcopy(case_index))

    def upload(self, store, n=400, upload_id="corpus-upload"):
        store.dispatch(12001, 12001, self.corpus_begin(n, upload_id))
        _, _, cases, docs = self.parts[n]
        for kind, parts in (("case_set", cases), ("document", docs)):
            for i, segment in enumerate(parts):
                self.call(store, 12001, "corpus_partition_put_" + kind + "_segment",
                          upload_id + "-" + kind + "-" + str(i), upload_id=upload_id, segment=segment)
        return self.call(store, 12001, "corpus_partition_commit", upload_id + "-commit", upload_id=upload_id)

    def read(self, store, ref, kind="corpus_index", index=None, uid=12004, request_id="corpus-read"):
        return self.call(store, uid, "corpus_partition_read", request_id,
                         policy_series_id=self.policy["policy_id"], corpus_ref=ref,
                         artifact_kind=kind, segment_index=index)

    def test_three_sizes_commit_restart_restore_and_lost_ack_replay(self):
        from gah.partitioned_scale_corpus import restore_scale_corpus
        from gah.run_contracts import content_ref
        saved = {}
        with self.open() as store:
            self.seed_policy(store)
            for n in (400, 800, 1600):
                upload_id = "size-" + str(n)
                saved[n] = self.upload(store, n, upload_id)
                again = self.call(store, 12001, "corpus_partition_commit", upload_id + "-commit", upload_id=upload_id)
                self.assertTrue(again["already_committed"])
                self.assertEqual(saved[n]["corpus_ref"], again["corpus_ref"])
                begun = store.dispatch(12001, 12001, self.corpus_begin(n, upload_id))
                self.assertTrue(begun["resumed"])
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_upload").fetchone()[0], 0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_segments").fetchone()[0], 0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_commits").fetchone()[0], 3)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM eval_adoptions").fetchone()[0], 0)
        with self.open() as store:
            for n in (400, 800, 1600):
                ref = saved[n]["corpus_ref"]
                parts = []
                for kind, source in zip(("corpus_index", "case_set_index", "case_set_segment", "document_segment"), self.parts[n]):
                    if kind.endswith("_index"):
                        response = self.read(store, ref, kind, request_id=str(n) + "-" + kind)
                        parts.append(response["artifact"])
                        self.assertFalse(response["ci_eligible"])
                    else:
                        parts.append([self.read(store, ref, kind, i, request_id=str(n)+"-"+kind+str(i))["artifact"]
                                      for i in range(len(source))])
                self.assertEqual(restore_scale_corpus(*parts), self.corpora[n])
                case_set = self.corpora[n]["case_set"]
                self.assertEqual(saved[n]["case_set_ref"], content_ref("case_set", case_set["case_set_id"], case_set))
            self.assertEqual(store._db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_os_roles_replay_binding_and_policy_revocation(self):
        with self.open() as store:
            self.seed_policy(store)
            begin = self.corpus_begin()
            for uid in (12002, 12003, 12004):
                with self.subTest(uid=uid), self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                    store.dispatch(uid, uid, begin)
            committed = self.upload(store)
            for uid in (12001, 12003, 12004):
                response = self.read(store, committed["corpus_ref"], uid=uid, request_id="role-"+str(uid))
                self.assertEqual(response["artifact"], self.parts[400][0])
            with self.assertRaisesRegex(AdoptionError, "^AUTHORITY_DENIED$"):
                self.read(store, committed["corpus_ref"], uid=12002, request_id="candidate-read")
            changed = deepcopy(begin)
            changed["expected_permission_generation"] = 1
            with self.assertRaises(AdoptionError):
                store.dispatch(12001, 12001, changed)
            self.call(store, 12004, "revoke_validation", "revoke-policy", validation_id="policy-validation")
            for action in (lambda: self.read(store, committed["corpus_ref"], uid=12004, request_id="role-12004"),
                           lambda: self.call(store, 12001, "corpus_partition_commit", "corpus-upload-commit", upload_id="corpus-upload")):
                with self.assertRaises(AdoptionError):
                    action()

    def test_source_changed_after_begin_rolls_back_domain_and_replay_marker(self):
        with self.open() as store:
            self.seed_policy(store)
            original = storage.handle
            digest = store._extension_digest
            changed = False
            def handler(*args, **kwargs):
                nonlocal changed
                result = original(*args, **kwargs)
                changed = True
                return result
            with patch.object(storage, "handle", side_effect=handler), patch.object(
                    authority, "source_digest", side_effect=lambda: "f"*64 if changed else digest):
                with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                    store.dispatch(12001, 12001, self.corpus_begin())
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_upload").fetchone()[0], 0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM idempotency WHERE request_id='corpus-upload-begin'").fetchone()[0], 0)

    def test_source_changed_after_commit_restores_staging_and_omits_commit(self):
        with self.open() as store:
            self.seed_policy(store)
            store.dispatch(12001, 12001, self.corpus_begin())
            for kind, parts in (("case_set", self.parts[400][2]), ("document", self.parts[400][3])):
                for i, segment in enumerate(parts):
                    self.call(store, 12001, "corpus_partition_put_"+kind+"_segment", kind+str(i),
                              upload_id="corpus-upload", segment=segment)
            original = storage.handle
            digest = store._extension_digest
            changed = False
            def handler(*args, **kwargs):
                nonlocal changed
                result = original(*args, **kwargs)
                changed = True
                return result
            with patch.object(storage, "handle", side_effect=handler), patch.object(
                    authority, "source_digest", side_effect=lambda: "f"*64 if changed else digest):
                with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                    self.call(store, 12001, "corpus_partition_commit", "rollback-commit", upload_id="corpus-upload")
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_upload").fetchone()[0], 1)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_commits").fetchone()[0], 0)
            self.assertEqual(store._db.execute("SELECT COUNT(*) FROM partition_scale_corpus_committed_segments").fetchone()[0], 0)
            result = self.call(store, 12001, "corpus_partition_commit", "rollback-commit", upload_id="corpus-upload")
            self.assertFalse(result["already_committed"])

    def test_extension_cannot_return_ci_eligible_success(self):
        with self.open() as store:
            value = request("corpus_partition_status", "false-success", upload_id="missing")
            response = {"schema_version": 1, "kind": "partitioned_corpus_store_result",
                        "action": value["action"], "request_id": value["request_id"], "ci_eligible": True}
            with patch.object(storage, "handle", return_value=response):
                with self.assertRaisesRegex(AdoptionError, "^EXTENSION_INVALID$"):
                    store.dispatch(12001, 12001, value)
