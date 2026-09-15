"""採択済み固定runで原子的開始の役割・現在条件・保存再配送を確認する。"""
from copy import deepcopy
import unittest
from tests import test_baseline_adoption_integration as seed
from gah.adoption import AdoptionError
from gah.run_contracts import content_ref


class ResourceStartIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.fixture=seed.BaselineAdoptionIntegrationTests('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.store=self.fixture.open();self.addCleanup(self.store.close)
        _,prepared,owner=self.fixture._prepare_contract_run(self.store,'atomic-integration')
        self.bound=prepared['bound_run'];self.entry=self.bound['plan']['entries'][0]
        material=next(m for m in prepared['pack']['materials'] if m['obligation_id']==self.entry['obligation_id'])
        self.request={'schema_version':1,'action':'resource_start','request_id':'atomic-start',
            'run_id':'atomic-integration','owner_id':owner['owner_id'],'operation_id':'atomic-operation',
            'entry':{k:self.entry[k] for k in ('obligation_id','case_id','trial_id','variant')},
            'scenario':material['scenario'],'expected_manifest_ref':content_ref('run_manifest','atomic-integration',self.bound['manifest'])}
    def test_operator_only_and_success_is_not_ci_permission(self):
        for uid in (12001,12002,12003):
            with self.assertRaises(AdoptionError):self.store.dispatch(uid,uid,self.request)
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM resource_operations').fetchone()[0],0)
        result=self.store.dispatch(12004,12004,self.request)
        self.assertEqual(result['operation']['entry'],self.entry);self.assertTrue(result['operation']['dispatch_intended'])
        self.assertFalse(result['ci_eligible'])
        with self.assertRaisesRegex(AdoptionError,'DISPATCH_NOT_PROVABLY_NEW'):
            self.store.dispatch(12004,12004,self.request)
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM resource_operations').fetchone()[0],1)
    def test_changed_manifest_and_expired_run_cannot_start(self):
        changed=deepcopy(self.request);changed['expected_manifest_ref']['digest']='f'*64
        with self.assertRaises(AdoptionError):self.store.dispatch(12004,12004,changed)
        self.fixture.clock.value=self.bound['manifest']['deadline']
        with self.assertRaises(AdoptionError):self.store.dispatch(12004,12004,self.request)
        self.assertEqual(self.store._db.execute('SELECT COUNT(*) FROM resource_operations').fetchone()[0],0)


if __name__=='__main__':unittest.main()
