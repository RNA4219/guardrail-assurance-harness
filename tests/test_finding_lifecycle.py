"""元runの実SQLite保存と、Finding管理の認証・原子的更新を検査する。"""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah.adoption import AdoptionError
from gah import finding_lifecycle as lifecycle, adoption_migrations as migrations
from gah.run_contracts import content_ref
from gah.resources import _packed
spec=importlib.util.spec_from_file_location('finding_scope_seed',ROOT/'tests/test_run_scope.py')
helpers=importlib.util.module_from_spec(spec);spec.loader.exec_module(helpers)
request=helpers.request


class FindingLifecycleIntegrationTests(unittest.TestCase):
    open=helpers.ScopedRunIntegrationTests.open
    prepare=helpers.ScopedRunIntegrationTests.prepare
    begin=helpers.ScopedRunIntegrationTests.begin
    complete=helpers.ScopedRunIntegrationTests.complete
    scoped=helpers.ScopedRunIntegrationTests.scoped
    gate_request=helpers.ScopedRunIntegrationTests.gate_request
    setUp=helpers.ScopedRunIntegrationTests.setUp

    @classmethod
    def setUpClass(cls):
        helpers.ScopedRunIntegrationTests.setUpClass.__func__(cls)
        helper=cls('runTest');helper.setUp();cls.addClassCleanup(helper.doCleanups)
        _,prepared,_=helper.scoped('finding-source')
        helper.complete(prepared,mutate=lambda record,observation:
            {'check':'FAIL'} if record['variant']=='candidate' else observation)
        rows=helper.store._db.execute("SELECT payload_json FROM authority_artifacts WHERE kind='finding' AND run_id='finding-source'").fetchall()
        findings=[json.loads(row[0]) for row in rows if 'status' in json.loads(row[0])]
        assert findings
        cls.finding=next(f for f in findings if f['status']=='OPEN')
        cls.finding_ref=content_ref('finding',cls.finding['finding_id'],cls.finding)
        cls.old_target=prepared['bound_run']['registry']['controls'][0]['target_ref']
        cls.source_bound=prepared['bound_run']
        target=Path(cls.temp.name)/'finding-seed.sqlite'
        with closing(sqlite3.connect(target)) as db:helper.store._db.backup(db)
        cls.seed_path=target;cls.at=helper.clock.value+1

    def req(self,action,name,**fields):
        return request(action,'finding-'+name,run_id='finding-source',finding_ref=deepcopy(self.finding_ref),**fields)

    def current(self):
        return self.store.dispatch(12004,12004,self.req('finding_current','current'))

    def start(self):
        return self.store.dispatch(12004,12004,self.req('finding_start','start',expected_state_ref=self.current()['state_ref']))

    def test_start_cas_replay_restart_keep_source_finding_immutable(self):
        initial=self.current();self.assertEqual(initial['state']['status'],'OPEN')
        req=self.req('finding_start','start',expected_state_ref=initial['state_ref'])
        for uid in (12001,12002,12003):
            with self.subTest(uid=uid),self.assertRaisesRegex(AdoptionError,'AUTHORITY_DENIED'):
                self.store.dispatch(uid,uid,req)
        started=self.store.dispatch(12004,12004,req)
        self.assertEqual(started['state']['status'],'IN_PROGRESS')
        self.assertEqual(self.store.dispatch(12004,12004,req),started)
        with self.assertRaisesRegex(AdoptionError,'FINDING_STATE_CONFLICT'):
            self.store.dispatch(12004,12004,{**req,'request_id':'stale-start'})
        self.store.close();self.store=self.open()
        self.assertEqual(self.current()['state'],started['state'])
        original=self.store.dispatch(12004,12004,request('run_artifact','original',run_id='finding-source',artifact_ref=self.finding_ref))
        self.assertEqual(original['artifact'],self.finding)
        self.assertFalse(self.current()['verification_current'])

    def test_request_rejects_same_target_and_confirm_requires_new_evidence(self):
        started=self.start();_,new,_=self.scoped('same-target')
        self.begin(new)
        req=self.req('finding_request_revalidation','revalidation',expected_state_ref=started['state_ref'],
            new_run_ref=content_ref('run_manifest','same-target',new['bound_run']['manifest']),changed_target_ref=self.old_target)
        with self.assertRaisesRegex(AdoptionError,'REVALIDATION_CONDITIONS_MISMATCH'):
            self.store.dispatch(12004,12004,req)
        confirm=self.req('finding_confirm','confirm',expected_state_ref=started['state_ref'])
        with self.assertRaisesRegex(AdoptionError,'AUTHORITY_DENIED'):
            self.store.dispatch(12004,12004,confirm)
        with self.assertRaisesRegex(AdoptionError,'FINDING_STATE_INVALID'):
            self.store.dispatch(12003,12003,confirm)
        self.assertEqual(self.current()['state'],started['state'])

    def test_origin_and_duplicate_sequence_tamper_are_rejected(self):
        self.start()
        self.store._db.execute("UPDATE idempotency SET actor_id='manager' WHERE request_id='finding-start'")
        with self.assertRaisesRegex(AdoptionError,'FINDING_ORIGIN_INVALID'):self.current()
        self.store._db.execute("UPDATE idempotency SET actor_id='operator' WHERE request_id='finding-start'")
        row=self.store._db.execute("SELECT * FROM authority_artifacts WHERE kind=?",(lifecycle.EVENT_KIND,)).fetchone()
        value=json.loads(row['payload_json']);value['state']['status']='VERIFIED';raw,digest=_packed(value)
        self.store._db.execute('INSERT INTO authority_artifacts VALUES(?,?,?,?,?)',(row['kind'],row['id'],digest,raw,row['run_id']))
        with self.assertRaises(AdoptionError):self.current()

    def test_failure_rolls_back_state_and_idempotency_then_can_retry(self):
        initial=self.current()
        self.store._db.execute("CREATE TRIGGER reject_finding BEFORE INSERT ON authority_artifacts WHEN NEW.kind='finding_management_event' BEGIN SELECT RAISE(ABORT,'synthetic'); END")
        with self.assertRaises(AdoptionError):self.start()
        self.assertEqual(self.current()['state_ref'],initial['state_ref'])
        self.assertIsNone(self.store._db.execute("SELECT 1 FROM idempotency WHERE request_id='finding-start'").fetchone())
        self.store._db.execute('DROP TRIGGER reject_finding')
        self.assertEqual(self.start()['state']['status'],'IN_PROGRESS')

    def test_cli_uses_fixed_roles_and_preserves_management_rejection(self):
        from tools.gah_finding import execute
        runtime=helpers.supervisor.Runtime(self.store)
        current,code=execute(runtime,self.req('finding_current','cli-current'))
        self.assertEqual(code,0);self.assertFalse(current['ci_eligible'])
        started,code=execute(runtime,self.req('finding_start','cli-start',expected_state_ref=current['state_ref']))
        self.assertEqual(code,0);self.assertEqual(started['state']['status'],'IN_PROGRESS')
        rejected,code=execute(runtime,self.req('finding_confirm','cli-confirm',expected_state_ref=started['state_ref']))
        self.assertEqual(code,2);self.assertEqual(rejected['reason'],'FINDING_STATE_INVALID')
        self.assertEqual(self.current()['state'],started['state'])

    def test_old_store_version_cannot_contain_finding_events(self):
        self.start();self.store.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",(migrations._V4_MIGRATED_SCOPE_EXTENSION_DIGEST,));db.commit()
        with self.assertRaisesRegex(migrations.MigrationError,'STORAGE_CORRUPT'):
            migrations.migrate_evaluation_store(self.path)


if __name__=='__main__':unittest.main()
