"""過去の無害な実行snapshotを構造入力として使い、現在の採択には使わない。"""
from copy import deepcopy
import hashlib,importlib.util,json,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah.contracts import ContractError
from gah.run_contracts import content_ref
from gah import following_contracts as candidate
EVIDENCE=ROOT/'docs/evidence/mvp-baseline-refresh-20260912'

def data():
    verification=json.loads((EVIDENCE/'verification.json').read_text('utf-8'))
    p=EVIDENCE/'runtime-observations.json'
    assert hashlib.sha256(p.read_bytes()).hexdigest()==verification['artifact_sha256']['runtime-observations.json']
    rows=json.loads(p.read_text('utf-8'))
    source=next(item['response'] for item in rows if item['action']=='run_prepare'
        and item['response'].get('bound_run',{}).get('manifest',{}).get('run_id')=='regression-runtime')
    baseline=next(item['response']['baseline'] for item in rows if item['action']=='baseline_current'
        and item['response'].get('baseline',{}).get('generation')==2)
    previous=source['bound_run']['contract'];following=deepcopy(previous)
    following.update(contract_id='fixture-contract-generation-3',generation=3)
    following['comparison']['baseline_ref']=content_ref('baseline',baseline['baseline_id'],baseline)
    return previous,following,baseline,source

class FollowingTransitionTests(unittest.TestCase):
    def setUp(self):self.previous,self.following,self.baseline,self.source=data()
    def build(self):
        return candidate.bind_following_transition(self.previous,self.following,baseline_record=self.baseline,
            baseline_source_bound=self.source['bound_run'],source_baseline_context=self.source['baseline_context'])
    def test_baseline_two_and_old_comparison_one_are_both_retained(self):
        result=self.build()
        self.assertEqual(result['schema_version'],2)
        self.assertEqual(result['source_baseline_context'],self.source['baseline_context'])
        self.assertNotEqual(result['source_baseline_context']['baseline_ref'],result['baseline_ref'])
        self.assertTrue(result['conditions_comparison']['same_measurement_conditions'])
        self.assertFalse(result['conditions_comparison']['same_evaluation_conditions'])
        self.assertEqual(len(result['source_bound']['plan']['entries']),30)
        self.assertFalse(result['authority_connected']);self.assertFalse(result['adoption_verified'])
    def test_generation_skip_or_reused_id_are_rejected(self):
        self.following['generation']=4
        with self.assertRaises(ContractError):self.build()
        self.following['generation']=3;self.following['contract_id']=self.previous['contract_id']
        with self.assertRaises(ContractError):self.build()
    def test_wrong_baseline_source_cannot_be_substituted(self):
        self.baseline['source_run_ref']['id']='other-run'
        self.following['comparison']['baseline_ref']=content_ref('baseline',self.baseline['baseline_id'],self.baseline)
        with self.assertRaises(ContractError):self.build()
    def test_comparison_change_or_missing_old_context_are_rejected(self):
        self.following['comparison']['baseline_ref']=deepcopy(self.source['baseline_context']['baseline_ref'])
        with self.assertRaises(ContractError):self.build()
        self.previous,self.following,self.baseline,self.source=data()
        self.source['baseline_context']=None
        with self.assertRaises(ContractError):self.build()
    def test_record_evaluator_and_repeat_are_bound_to_source(self):
        for field in ('evaluator_refs','repeat_config_ref'):
            self.previous,self.following,self.baseline,self.source=data()
            if field=='evaluator_refs':self.baseline[field][0]['digest']='a'*64
            else:self.baseline[field]['digest']='a'*64
            self.following['comparison']['baseline_ref']=content_ref('baseline',self.baseline['baseline_id'],self.baseline)
            with self.subTest(field=field):
                with self.assertRaises(ContractError):self.build()
    def test_outputs_are_independent_and_never_ci_evidence(self):
        result=self.build();saved=deepcopy(self.source)
        result['source_bound']['plan']['entries'].clear()
        self.assertEqual(self.source,saved)
        self.assertFalse(result['ci_eligible'])

    def test_comparison_context_reference_cannot_be_substituted(self):
        self.baseline['comparison_context_ref']['digest']='a'*64
        self.following['comparison']['baseline_ref']=content_ref('baseline',self.baseline['baseline_id'],self.baseline)
        with self.assertRaises(ContractError):self.build()
    def test_malformed_source_and_boolean_generation_fail_with_contract_error(self):
        for change in (lambda: self.source.update(bound_run=[]),
                       lambda: self.following.update(generation=True)):
            self.previous,self.following,self.baseline,self.source=data()
            change()
            with self.assertRaises(ContractError):self.build()
    def runs(self):
        return candidate.build_following_runs(self.previous,self.following,baseline_record=self.baseline,
            source_prepared={k:self.source[k] for k in ('bound_run','baseline_context','materialization')},
            now=self.source['bound_run']['manifest']['created_at']+1,old_run_id='following-old',new_run_id='following-new')
    def test_materialization_has_thirty_on_each_side_and_unchanged_payloads(self):
        result=self.runs();source=self.source['bound_run']
        self.assertEqual(result['old']['baseline_context'],self.source['baseline_context'])
        self.assertEqual(result['old']['bound_run']['plan']['entries'],source['plan']['entries'])
        for side in ('old','new'):
            self.assertEqual(len(result[side]['bound_run']['plan']['entries']),30)
            self.assertEqual(len(result[side]['materialization']['manifest']['records']),30)
            self.assertEqual(result[side]['materialization']['pack_ref'],self.source['materialization']['pack_ref'])
        self.assertNotEqual(result['old']['baseline_context']['baseline_ref'],result['new']['baseline_context']['baseline_ref'])
        expected=[r for r in self.source['materialization']['manifest']['records'] if r['variant']=='candidate']
        actual=[r for r in result['new']['materialization']['manifest']['records'] if r['variant']=='baseline']
        self.assertEqual([{**r,'variant':'candidate'} for r in actual],expected)
    def test_materialization_duplicate_and_missing_record_rejected(self):
        for mutate in (lambda rs: rs.pop(),lambda rs: rs.__setitem__(0,deepcopy(rs[1]))):
            self.previous,self.following,self.baseline,self.source=data()
            material=self.source['materialization'];mutate(material['manifest']['records'])
            material['manifest_ref']=content_ref('fixture_manifest',material['manifest']['materialization_id'],material['manifest'])
            with self.assertRaises(ContractError):self.runs()

if __name__=='__main__':unittest.main(verbosity=2)
