"""認証された操作読取りと、保存済みbaseline更新版の明示移行を検査する。"""
from contextlib import closing
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sqlite3
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('operation_integration_seed', ROOT/'tests/test_baseline_refresh.py')
seed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(seed)
from gah.adoption import AdoptionError
from gah import adoption_migrations as migrations
from gah.run_contracts import content_ref
request = seed.request


class OperationIntegrationTests(unittest.TestCase):
    setUp = seed.BaselineRefreshTests.setUp
    open = seed.BaselineRefreshTests.open
    prepare = seed.BaselineRefreshTests.prepare
    begin = seed.BaselineRefreshTests.begin
    complete = seed.BaselineRefreshTests.complete
    completed = seed.BaselineRefreshTests.completed
    gate_request = seed.BaselineRefreshTests.gate_request
    gate = seed.BaselineRefreshTests.gate
    source = seed.BaselineRefreshTests.source
    propose = seed.BaselineRefreshTests.propose
    validate = seed.BaselineRefreshTests.validate
    adopt = seed.BaselineRefreshTests.adopt
    current = seed.BaselineRefreshTests.current

    @classmethod
    def setUpClass(cls):
        seed.BaselineRefreshTests.setUpClass.__func__(cls)

    def reserved(self):
        prepared = self.prepare('inspect-run')
        owner = self.begin(prepared)
        record = prepared['materialization']['manifest']['records'][0]
        entry = next(e for e in prepared['bound_run']['plan']['entries']
            if all(e[k] == record[k] for k in ('obligation_id','case_id','trial_id','variant')))
        self.store.dispatch(12004,12004,request('resource_reserve','inspect-reserve',**owner,
            operation_id='inspect-op',entry={k:entry[k] for k in ('obligation_id','case_id','trial_id','variant')},
            scenario=record['scenario']))
        query=request('resource_operation','inspect-query',run_id='inspect-run',operation_id='inspect-op',
            expected_manifest_ref=content_ref('run_manifest','inspect-run',prepared['bound_run']['manifest']))
        return prepared, owner, query, entry

    def test_roles_same_request_is_fresh_and_stop_not_usage(self):
        prepared,owner,query,entry=self.reserved()
        for uid in (12001,12002):
            with self.assertRaisesRegex(AdoptionError,'^AUTHORITY_DENIED$'):
                self.store.dispatch(uid,uid,query)
        first=self.store.dispatch(12004,12004,query)
        self.assertEqual(first['entry'],entry)
        self.assertFalse(first['dispatch_intended'])
        self.store.dispatch(12004,12004,request('resource_dispatch','inspect-dispatch',**owner,operation_id='inspect-op'))
        self.assertTrue(self.store.dispatch(12004,12004,query)['dispatch_intended'])
        self.store.dispatch(12003,12003,request('resource_observe','inspect-stop',run_id='inspect-run',
            operation_id='inspect-op',event_id='inspect-stopped',stopped=True,usage=None))
        read=self.store.dispatch(12003,12003,{**query,'request_id':'validator-inspect'})
        self.assertTrue(read['stopped']);self.assertFalse(read['settled']);self.assertIsNone(read['usage'])
        self.assertFalse(read['ci_eligible']);self.assertNotIn('dispatch_allowed',read)
        self.assertFalse(first['stopped'])

    def test_revoked_start_basis_does_not_hide_saved_operation(self):
        prepared,owner,query,entry=self.reserved()
        old=self.store._db.execute('SELECT run_id FROM baseline_proposals WHERE expected_generation=0').fetchone()[0]
        self.store.dispatch(12004,12004,request('evidence_revoke','inspect-revoke',run_id=old))
        with self.assertRaises(AdoptionError):
            self.store.dispatch(12004,12004,request('resource_dispatch','revoked-dispatch',**owner,operation_id='inspect-op'))
        result=self.store.dispatch(12004,12004,query)
        self.assertEqual(result['entry'],entry);self.assertFalse(result['dispatch_intended'])
        self.store.dispatch(12004,12004,request('resource_cancel','inspect-cancel',**owner))
        self.assertTrue(self.store.dispatch(12004,12004,query)['released'])

    def test_wrong_reference_and_saved_binding_are_rejected(self):
        prepared,owner,query,entry=self.reserved()
        with self.assertRaisesRegex(AdoptionError,'^BINDING_MISMATCH$'):
            self.store.dispatch(12004,12004,{**query,'expected_manifest_ref':{**query['expected_manifest_ref'],'digest':'a'*64}})
        row=dict(self.store._db.execute("SELECT * FROM resource_bindings WHERE operation_id='inspect-op'").fetchone())
        for column,value in (('entry_digest','b'*64),('scenario','constraint:C01:bad'),('run_id','other-run')):
            with self.subTest(column=column):
                self.store._db.execute('UPDATE resource_bindings SET '+column+"=? WHERE operation_id='inspect-op'",(value,))
                with self.assertRaises(AdoptionError):self.store.dispatch(12004,12004,query)
                self.store._db.execute('UPDATE resource_bindings SET '+column+"=? WHERE operation_id='inspect-op'",(row[column],))
        self.assertFalse(self.store.dispatch(12004,12004,query)['dispatch_intended'])

    def refresh_store(self):
        prepared=self.source();self.propose();self.validate();self.adopt()
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",(migrations._V4_REFRESH_EXTENSION_DIGEST,))
        return prepared

    def test_migration_preserves_both_generations_and_outputs(self):
        prepared=self.refresh_store()
        prior=tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest'))
        self.store.close()
        migrated=migrations.migrate_evaluation_store(self.path)
        self.assertTrue(migrated['changed'])
        self.assertEqual(migrated['predecessor_extension_digest'],migrations._V4_REFRESH_EXTENSION_DIGEST)
        self.store=self.open()
        self.assertEqual(tuple(self.store._db.execute('SELECT * FROM authority_artifacts ORDER BY kind,id,digest')),prior)
        self.assertEqual(self.current()['generation'],2)
        self.gate(prepared,0)

    def test_migration_preserves_revocation_without_refreshing_evidence(self):
        prepared=self.source();self.propose();self.validate();self.adopt()
        self.store.dispatch(12004,12004,request('evidence_revoke','migration-revoke',run_id='completed'))
        self.store._db.execute("UPDATE adoption_config SET value=? WHERE key='extension_digest'",(migrations._V4_REFRESH_EXTENSION_DIGEST,))
        self.store.close();migrations.migrate_evaluation_store(self.path);self.store=self.open()
        self.assertFalse(self.current()['valid'])
        self.gate(prepared,1)

    def test_corrupt_refresh_history_cannot_migrate(self):
        self.refresh_store()
        self.store._db.execute("UPDATE baseline_adoptions SET baseline_digest=? WHERE generation=1",('d'*64,))
        self.store.close()
        with self.assertRaises(migrations.MigrationError):migrations.migrate_evaluation_store(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT value FROM adoption_config WHERE key='extension_digest'").fetchone()[0],migrations._V4_REFRESH_EXTENSION_DIGEST)


if __name__=='__main__':unittest.main(verbosity=2)
