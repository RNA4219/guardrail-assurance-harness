"""分割入力と旧・新候補が通常の文書上限内で往復することを検査する。"""
from copy import deepcopy
from pathlib import Path
import sqlite3
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from gah import candidate_sections, partitioned_llm_admission as io
from gah.adoption import AdoptionError
from gah.contracts import ContractError, MAX_DOCUMENT_BYTES
from gah.partitioned_llm_materialization import build
from gah.partitioned_llm_transitions import build as transition, rebind
from gah.partitioned_run_contracts import materialize_partitioned_run
from gah.policy import initial_policy_profile
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from tests import test_partitioned_contract_updates as fixtures


class PartitionedPreparedIOTests(unittest.TestCase):
    def prepared(self, count=400):
        value = build(initial_policy_profile(), case_count=count, policy_generation=1,
            run_id='io-source', now=1000, target_version='baseline-v1',
            image_id=fixtures.IMAGE_ID, worker_digest=fixtures.WORKER_DIGEST,
            isolation_profile=fixtures.ISOLATION)
        value['bound_run'] = materialize_partitioned_run(value['manifest'], value['contract'],
            value['plan_index'], value['plan_segments'], value['policy'], value['registry'], value['case_set'])
        return value

    def db(self):
        db = sqlite3.connect(':memory:'); self.addCleanup(db.close); db.row_factory=sqlite3.Row
        db.execute('CREATE TABLE eval_objects(kind,id,digest,payload_json,PRIMARY KEY(kind,id))')
        db.execute('CREATE TABLE authority_artifacts(kind,id,digest,payload_json,run_id,PRIMARY KEY(kind,id,digest))')
        db.execute('BEGIN')
        return db

    def test_prepared_round_trip_and_wrong_artifact_rejected(self):
        value = self.prepared(); db=self.db(); root=io.store_prepared(db,value)
        self.assertEqual(io.load_prepared(db,root),value)
        from gah.evaluation_authority import _object
        self.assertEqual(io.resolve_prepared(root,lambda ref:_object(db,ref)),value)
        bad=deepcopy(root); bad['binding']['manifest_ref']['digest']='0'*64
        with self.assertRaises((AdoptionError,ContractError)):
            io.resolve_prepared(bad,lambda ref:_object(db,ref))
        with self.assertRaises((AdoptionError,ContractError)):
            io.resolve_prepared(root,lambda ref:{'unrelated':True})

    def test_verify_expected_prepared_checks_compact_root_and_every_artifact(self):
        value = self.prepared(); db = self.db(); root = io.store_prepared(db, value)
        self.assertIs(io.verify_expected_prepared(db, root, value), value)

        extra = deepcopy(root); extra["unexpected"] = True
        with self.assertRaises((AdoptionError, ContractError)):
            io.verify_expected_prepared(db, extra, value)
        bad_ref = deepcopy(root)
        first = next(ref for ref in bad_ref["artifact_refs"].values()
                     for ref in (ref if type(ref) is list else [ref]) if ref is not None)
        first["digest"] = "0" * 64
        with self.assertRaises((AdoptionError, ContractError)):
            io.verify_expected_prepared(db, bad_ref, value)

        artifact_ref, _ = next(iter(io._prepared_parts(value)[1].values()))
        db.execute("DELETE FROM eval_objects WHERE kind=? AND id=?",
                   (artifact_ref["kind"], artifact_ref["id"]))
        with self.assertRaises((AdoptionError, ContractError)):
            io.verify_expected_prepared(db, root, value)

    def test_verify_expected_prepared_rejects_missing_or_corrupt_stored_root(self):
        value = self.prepared(); db = self.db(); root = io.store_prepared(db, value)
        db.execute("DELETE FROM eval_objects WHERE kind=? AND id=?",
                   ("partitioned_prepared_run", root["run_id"]))
        with self.assertRaises((AdoptionError, ContractError)):
            io.verify_expected_prepared(db, root, value)

        db2 = self.db(); root2 = io.store_prepared(db2, value)
        db2.execute("UPDATE eval_objects SET payload_json=? WHERE kind=? AND id=?",
                    ('{"tampered":true}', "partitioned_prepared_run", root2["run_id"]))
        with self.assertRaises((AdoptionError, ContractError)):
            io.verify_expected_prepared(db2, root2, value)

    def test_verify_expected_prepared_rejects_corrupt_artifact_bytes(self):
        value = self.prepared(); db = self.db(); root = io.store_prepared(db, value)
        artifact_ref, _ = next(iter(io._prepared_parts(value)[1].values()))
        db.execute("UPDATE eval_objects SET payload_json=? WHERE kind=? AND id=?",
                   ('{"tampered":true}', artifact_ref["kind"], artifact_ref["id"]))
        with self.assertRaises((AdoptionError, ContractError)):
            io.verify_expected_prepared(db, root, value)

    def test_400_and_1600_candidates_round_trip_without_large_wire_documents(self):
        for count in (400,1600):
            with self.subTest(case_count=count):
                source=self.prepared(count)
                record,previous,following=fixtures.PartitionedContractUpdateTests._baseline_and_next(source['bound_run'])
                runs=transition(previous,following,baseline_record=record,source_prepared=source,
                    now=1100,old_run_id='io-old',new_run_id='io-new')
                self.assertEqual(len(runs['old']['bound_run']['plan']['entries']),count)
                self.assertEqual(len(runs['new']['bound_run']['plan']['entries']),count*2)
                db=self.db(); candidate={'candidate_id':'io-candidate','runs':runs}
                stored={'candidate_id':'io-candidate','runs':candidate_sections.pack(db,'io-candidate',runs)}
                self.assertLess(len(canonical_bytes(stored)),MAX_DOCUMENT_BYTES)
                self.assertEqual(candidate_sections.reference(candidate),content_ref('contract_candidate','io-candidate',stored))
                self.assertEqual(candidate_sections.unpack(db,'io-candidate',stored['runs']),runs)
                normal=rebind(runs['new'],run_id='io-normal',now=1200)
                self.assertEqual(io.load_prepared(db,io.store_prepared(db,normal)),normal)
                sizes=[len(row[0].encode()) for row in db.execute('SELECT payload_json FROM eval_objects')]
                sizes.extend(len(row[0].encode()) for row in db.execute('SELECT payload_json FROM authority_artifacts'))
                self.assertLessEqual(max(sizes),MAX_DOCUMENT_BYTES)


if __name__ == '__main__':
    unittest.main()
