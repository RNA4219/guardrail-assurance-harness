"""baseline 2から契約3へ進む認証・実SQLiteの統合試験。Dockerは起動しない。"""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
spec=importlib.util.spec_from_file_location('following_seed',ROOT/'tests/test_baseline_refresh.py')
seed=importlib.util.module_from_spec(spec);spec.loader.exec_module(seed)
reg=seed.seed.seed
from gah.adoption import AdoptionError
from gah import adoption_migrations as migrations
from gah import fixture_admission
from gah.run_contracts import content_ref
request=seed.request

class FollowingContractIntegrationTests(unittest.TestCase):
    open=reg.RegressionIntegrationTests.open
    prepare=reg.RegressionIntegrationTests.prepare
    begin=reg.RegressionIntegrationTests.begin
    complete=reg.RegressionIntegrationTests.complete
    gate_request=reg.RegressionIntegrationTests.gate_request
    gate=seed.seed.CancellationIntegrationTests.gate

    @classmethod
    def setUpClass(cls):
        print('following setup: initial contract and baseline',flush=True)
        cls.addClassCleanup(seed.BaselineRefreshTests.doClassCleanups)
        seed.BaselineRefreshTests.setUpClass()
        helper=seed.BaselineRefreshTests('runTest');helper.setUp()
        cls.addClassCleanup(helper.doCleanups)
        cls.temp=tempfile.TemporaryDirectory();cls.addClassCleanup(cls.temp.cleanup)
        helper.source();helper.propose();helper.validate();helper.adopt()
        store=helper.store
        cls.preceding_path=Path(cls.temp.name)/'preceding.sqlite'
        with closing(sqlite3.connect(cls.preceding_path)) as target:store._db.backup(target)
        baseline=helper.current()['baseline']
        previous=json.loads(store._db.execute('SELECT payload_json FROM eval_current').fetchone()[0])
        following=deepcopy(previous);following.update(contract_id='fixture-contract-generation-3',generation=3)
        following['comparison']['baseline_ref']=content_ref('baseline',baseline['baseline_id'],baseline)
        store.dispatch(12001,12001,request('contract_propose','following-propose',proposal_id='following-proposal',
            series_id='fixture-contract-series',expected_generation=2,contract=following))
        cls.prepare_request=request('contract_candidate_prepare','following-prepare',proposal_id='following-proposal',
            baseline_series_id='fixture-baseline-series',expected_contract_ref=content_ref('evaluation_contract',previous['contract_id'],previous),
            expected_baseline_ref=following['comparison']['baseline_ref'],candidate_id='following-candidate',
            old_run_id='following-old',new_run_id='following-new')
        print('following setup: prepare',flush=True)
        cls.candidate=store.dispatch(12003,12003,cls.prepare_request)
        cls.values={'candidate_id':'following-candidate','old_run_id':'following-old','new_run_id':'following-new'}
        observed=[0]
        def observation(function,code,state):
            result=function(code,state);observed[0]+=1
            if observed[0]%10==0:print('following setup: observed '+str(observed[0]),flush=True)
            return result
        worker=SimpleNamespace(
            _constraint_observation=lambda c,s:observation(helper.worker._constraint_observation,c,s),
            _mutation_observation=lambda c,s:observation(helper.worker._mutation_observation,c,s))
        fixture=SimpleNamespace(worker=worker,clock=helper.clock)
        for side in ('old','new'):
            print('following setup: execute '+side,flush=True)
            reg.helpers._HELPERS.TransitionCandidateIntegrationTests._run_side(store,fixture,cls.candidate,cls.values,side)
        cls.contract_ref=content_ref('evaluation_contract',following['contract_id'],following)
        cls.seed_path=Path(cls.temp.name)/'following.sqlite'
        with closing(sqlite3.connect(cls.seed_path)) as target:store._db.backup(target)
        cls.at=helper.clock.value+1;cls.worker=helper.worker
        print('following setup: ready',flush=True)

    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.path=Path(temp.name)/'test.sqlite'
        with closing(sqlite3.connect(self.seed_path)) as source,closing(sqlite3.connect(self.path)) as target:source.backup(target)
        self.clock=reg.Clock(self.at);self.store=self.open()

    def validate(self):
        return self.store.dispatch(12003,12003,request('contract_candidate_validate','following-validate',
            candidate_id='following-candidate',validation_id='following-validation'))

    def adopt_request(self):
        return request('contract_candidate_adopt','following-adopt',candidate_id='following-candidate',
            validation_id='following-validation',expected_contract_generation=2,expected_baseline_generation=2)

    def adopt(self):return self.store.dispatch(12001,12001,self.adopt_request())

    def current(self):
        return self.store.dispatch(12004,12004,request('contract_current','following-current',series_id='fixture-contract-series'))

    def test_contract_three_regular_run_ci_and_restart(self):
        self.assertEqual(self.current()['generation'],2)
        before=tuple(self.store._db.execute('SELECT * FROM authority_run_receipts ORDER BY run_id'))
        self.assertTrue(self.validate()['passed']);self.assertEqual(self.adopt()['generation'],3)
        self.assertTrue(self.current()['valid'])
        self.assertEqual(tuple(self.store._db.execute('SELECT * FROM authority_run_receipts ORDER BY run_id')),before)
        prepared=self.prepare('following-normal');self.complete(prepared);self.gate(prepared,0)
        receipt=json.loads(self.store._db.execute(
            "SELECT payload_json FROM authority_run_receipts WHERE run_id='following-normal'").fetchone()[0])
        self.assertTrue(receipt['input_materialization_verified'])
        altered=deepcopy(prepared['bound_run'])
        altered['plan']['entries'][0]['target_ref']['digest']='0'*64
        with self.assertRaises(AdoptionError) as error:
            fixture_admission.materialized_run(self.store._db,altered,self.clock.value)
        self.assertEqual(error.exception.code,'REGRESSION_BINDING_INVALID')
        outputs=tuple(self.store._db.execute("SELECT * FROM authority_artifacts WHERE run_id='following-normal' ORDER BY kind,id,digest"))
        self.store.close();self.store=self.open()
        self.assertTrue(self.current()['valid']);self.gate(prepared,0)
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM authority_artifacts WHERE run_id='following-normal' ORDER BY kind,id,digest")),outputs)
        self._check_following_baseline(prepared)
        self.store.dispatch(12004,12004,request('evidence_revoke','following-revoke-source',run_id='completed'))
        self.assertFalse(self.current()['valid']);self.gate(prepared,1)
        self.assertEqual(tuple(self.store._db.execute("SELECT * FROM authority_artifacts WHERE run_id='following-normal' ORDER BY kind,id,digest")),outputs)

    def _check_following_baseline(self, prepared):
        series='fixture-baseline-series'
        artifacts=tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest'))
        prior=json.loads(self.store._db.execute('SELECT baseline_json FROM baseline_current').fetchone()[0])
        proposed=request('baseline_propose','following-baseline-propose',proposal_id='following-baseline',
            series_id=series,run_id='following-normal',expected_generation=2)
        self.assertEqual(self.store.dispatch(12001,12001,proposed)['generation'],3)
        validation=request('baseline_validate','following-baseline-validate',
            proposal_id='following-baseline',validation_id='following-baseline-validation')
        with self.assertRaises(AdoptionError):self.store.dispatch(12001,12001,validation)
        self.assertTrue(self.store.dispatch(12003,12003,validation)['passed'])
        adoption=request('baseline_adopt','following-baseline-adopt',proposal_id='following-baseline',
            validation_id='following-baseline-validation',expected_generation=2)
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12001,12001,{**adoption,'request_id':'following-baseline-stale','expected_generation':1})
        self.store._db.execute("CREATE TRIGGER fail_following_baseline BEFORE UPDATE ON baseline_current WHEN NEW.generation=3 BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(AdoptionError):self.store.dispatch(12001,12001,adoption)
        self.assertEqual(self.store._db.execute('SELECT generation FROM baseline_current').fetchone()[0],2)
        self.assertIsNone(self.store._db.execute('SELECT 1 FROM baseline_adoptions WHERE generation=3').fetchone())
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='following-baseline-adopt'").fetchone())
        self.store._db.execute('DROP TRIGGER fail_following_baseline')
        self.assertEqual(self.store.dispatch(12001,12001,adoption)['generation'],3)
        def current():
            return self.store.dispatch(12004,12004,request('baseline_current','following-baseline-current',series_id=series))
        value=current();self.assertTrue(value['valid'],value);self.assertEqual(value['generation'],3)
        latest=value['baseline']
        def pinned(record,action='baseline_resolve'):
            return self.store.dispatch(12004,12004,request(action,action+'-'+record['baseline_id'],series_id=series,
                expected_baseline_ref=content_ref('baseline',record['baseline_id'],record),expected_contract_ref=record['contract_ref']))
        self.assertTrue(pinned(prior)['use']);self.assertTrue(pinned(latest)['use'])
        self.assertEqual(prepared['bound_run']['manifest']['baseline_ref'],content_ref('baseline',prior['baseline_id'],prior))
        self.assertEqual(tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest')),artifacts)
        self.gate(prepared,0)
        self.store.close();self.store=self.open()
        self.assertTrue(current()['valid']);self.assertEqual(self.store.dispatch(12001,12001,adoption)['generation'],3)
        # 合成fixtureの旧版識別子。実旧runtimeの移行証拠とは区別する。
        from tests.test_following_migrations import rows as stored_rows
        before_migration=stored_rows(self.store._db)
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",
            (migrations._V4_FOLLOWING_EXTENSION_DIGEST,))
        self.store.close()
        migrated=migrations.migrate_evaluation_store(self.path)
        self.assertFalse(migrated['ci_eligible'])
        self.assertEqual(migrated['predecessor_extension_digest'],migrations._V4_FOLLOWING_EXTENSION_DIGEST)
        self.store=self.open()
        self.assertEqual(stored_rows(self.store._db),before_migration)
        self.gate(prepared,0)
        pinned(latest,'baseline_revoke_ref')
        self.assertFalse(current()['valid']);self.assertTrue(pinned(prior)['use']);self.gate(prepared,0)
        pinned(prior,'baseline_revoke_ref')
        self.assertFalse(self.current()['valid']);self.gate(prepared,1)
        self.assertEqual(tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest')),artifacts)

    def test_refresh_rejects_source_using_a_different_baseline(self):
        with self.assertRaises(AdoptionError) as error:
            self.store.dispatch(12001,12001,request('baseline_propose','following-baseline-wrong-source',
                proposal_id='wrong-source',series_id='fixture-baseline-series',run_id='completed',expected_generation=2))
        self.assertEqual(error.exception.code,'GENERATION_CONFLICT')
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM baseline_proposals WHERE proposal_id='wrong-source'").fetchone())

    def test_wrong_roles_and_expected_generations_do_not_advance(self):
        self.validate()
        for uid in (12002,12003,12004):
            with self.subTest(uid=uid),self.assertRaises(AdoptionError):self.store.dispatch(uid,uid,self.adopt_request())
        for field in ('expected_contract_generation','expected_baseline_generation'):
            value=self.adopt_request();value[field]=1;value['request_id']='wrong-'+field
            with self.subTest(field=field),self.assertRaises(AdoptionError):self.store.dispatch(12001,12001,value)
        self.assertEqual(self.current()['generation'],2)
        self.assertEqual(self.adopt()['generation'],3)

    def test_adoption_write_failure_rolls_back_and_can_retry(self):
        self.validate()
        self.store._db.execute("CREATE TRIGGER fail_following BEFORE INSERT ON eval_adoptions WHEN NEW.generation=3 BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(AdoptionError):self.adopt()
        self.assertEqual(self.current()['generation'],2)
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='following-adopt'").fetchone())
        self.store._db.execute('DROP TRIGGER fail_following')
        self.assertEqual(self.adopt()['generation'],3)

    def test_source_revocation_prevents_adoption(self):
        self.validate()
        self.store.dispatch(12004,12004,request('evidence_revoke','following-revoke',run_id='completed'))
        with self.assertRaises(AdoptionError):self.adopt()
        self.assertEqual(self.current()['generation'],2)

    def test_prior_version_upgrade_rejects_following_records(self):
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",(migrations._V4_SUPERVISOR_EXTENSION_DIGEST,))
        self.store.close()
        with self.assertRaises(migrations.MigrationError):migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0],migrations._V4_SUPERVISOR_EXTENSION_DIGEST)
        with closing(sqlite3.connect(self.preceding_path)) as source,closing(sqlite3.connect(self.path)) as target:source.backup(target)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",(migrations._V4_SUPERVISOR_EXTENSION_DIGEST,));db.commit()
        self.assertTrue(migrations.migrate_evaluation_store(self.path)['changed'])
        self.store=self.open();self.assertTrue(self.current()['valid'])

if __name__=='__main__':unittest.main(verbosity=2)
