"""固定adapter出力を正規化・SQLite保存・判定へ通す。OS実行の証明は別。

自作の固定三義務だけを使い、raw digestと保存Attemptの対応を検査する。
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from tests import test_run_evidence as seed, test_promptfoo_adapter as prompt
from gah.contracts import ContractError
from gah.normalized import normalize_generic
from gah.promptfoo_adapter import normalize_promptfoo, PromptfooAdapterError
from gah.run_evidence import RunEvidenceStore, bound_bundle_digest


class AdapterEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory();self.addCleanup(self.folder.cleanup)
        self.fixtures,self.plan,self.bound=seed.bound_fixture()
        self.fixtures['contract_ref_digest']=self.bound['manifest']['contract_ref']['digest']
        self.fixtures['policy_ref_digest']=self.bound['manifest']['policy_ref']['digest']
        root=Path(__file__).resolve().parents[1]
        self.digests={name:hashlib.sha256((root/'src/gah'/file).read_bytes()).hexdigest()
            for name,file in [('generic','normalized.py'),('promptfoo','promptfoo_adapter.py')]}
        self.profile={'fixture_digest':seed.ZERO,'adapter_digests':sorted(self.digests.values()),'isolation_digest':'2'*64}

    def open(self,name):
        return RunEvidenceStore(Path(self.folder.name)/(name+'.sqlite'),clock=lambda:100,
            allowed_bindings={'run-1':bound_bundle_digest(self.bound)})

    def inputs(self,adapter,mode,*,failure=False):
        attempt=seed.attempt_for(self.fixtures,self.plan,'obligation-'+mode,attempt_id='attempt-'+mode,mode=mode)
        binding=deepcopy(attempt['expected_binding']);binding['adapter_digest']=self.digests[adapter]
        observations={'constraint':{'check':'FAIL' if failure else 'PASS'},
            'mutation':{'baseline':'PASS','mutation_applied':True,'reached':True,'detected':True,'unrelated_failure':False},
            'llm':{'detection':'detect','deviation':False}}[mode]
        envelope={'schema_version':1,'kind':'gah_generic_result','binding':binding,'mode':mode,'observations':observations}
        if adapter=='generic':
            raw=json.dumps(envelope,sort_keys=True,separators=(',',':')).encode()
        else:
            raw=prompt.document(prompt.row(gah=binding,output=envelope),metadata={'promptfooVersion':'0.123.0'})
        attempt['expected_binding']=binding
        return attempt,raw

    def normalize(self,adapter,raw,binding):
        if adapter=='generic':
            return normalize_generic(raw,binding,execution_status='COMPLETED',exit_code=0,stop_confirmed=True)
        return normalize_promptfoo(raw,expected_binding=binding,expected_identity=prompt.identity())

    def evaluate(self,adapter,*,failure=False):
        name=adapter+('-fail' if failure else '-pass');observations=[]
        with self.open(name) as store:
            store.start_run(self.bound,self.profile)
            for mode in ('constraint','mutation','llm'):
                attempt,raw=self.inputs(adapter,mode,failure=failure and mode=='constraint')
                result=self.normalize(adapter,raw,attempt['expected_binding']);attempt['result']=result
                raw_path=Path(self.folder.name)/(name+'-'+mode+'.json');raw_path.write_bytes(raw)
                receipt=store.record_attempt(attempt);self.assertTrue(receipt['accepted'])
                saved=store.get_attempt(attempt['attempt_id'])['attempt']['result']
                self.assertEqual(saved['raw_digest'],hashlib.sha256(raw_path.read_bytes()).hexdigest())
                observations.append({k:v for k,v in saved.items() if k not in ('binding','raw_digest')})
            terminal=store.finalize('run-1');self.assertFalse(terminal['ci_eligible'])
            self.assertFalse(terminal['authority_connected'])
        with self.open(name) as store:
            self.assertEqual(store.get_terminal('run-1'),terminal)
            self.assertEqual(store.finalize('run-1'),terminal)
        return observations,terminal['decision']

    def test_both_adapters_preserve_semantics_raw_links_and_saved_decision(self):
        generic,gd=self.evaluate('generic');promptfoo,pd=self.evaluate('promptfoo')
        self.assertEqual(generic,promptfoo)
        self.assertEqual(gd,pd)
        self.assertNotIn(gd['assurance'],('HOLD','DEGRADED'))

    def test_external_success_does_not_hide_a_constraint_violation(self):
        generic,gd=self.evaluate('generic',failure=True);promptfoo,pd=self.evaluate('promptfoo',failure=True)
        self.assertEqual(generic,promptfoo);self.assertEqual(gd,pd)
        self.assertEqual(generic[0]['observation'],'FAIL')
        self.assertIn(gd['assurance'],('HOLD','DEGRADED'))

    def test_unsupported_and_malformed_outputs_leave_required_evidence_missing(self):
        for adapter in ('generic','promptfoo'):
            attempt,raw=self.inputs(adapter,'constraint')
            parsed=json.loads(raw)
            if adapter=='generic':parsed['schema_version']=2
            else:parsed['metadata']['promptfooVersion']='0.123.1'
            for suffix,payload in [('version',json.dumps(parsed).encode()),('malformed',b'{invalid')]:
                with self.open(adapter+'-'+suffix) as store:
                    store.start_run(self.bound,self.profile)
                    with self.assertRaises((ContractError,PromptfooAdapterError)):
                        result=self.normalize(adapter,payload,attempt['expected_binding'])
                        store.record_attempt({**attempt,'result':result})
                    self.assertEqual(store._db.execute('SELECT COUNT(*) FROM attempts WHERE run_id=?',('run-1',)).fetchone()[0],0)
                    terminal=store.finalize('run-1')
                    self.assertIn(terminal['decision']['assurance'],('HOLD','UNKNOWN'))
                    self.assertFalse(terminal['ci_eligible'])

    def test_execution_error_is_not_a_successful_observation(self):
        for adapter in ('generic','promptfoo'):
            attempt,raw=self.inputs(adapter,'constraint')
            if adapter=='generic':
                result=normalize_generic(raw,attempt['expected_binding'],execution_status='FAILED',exit_code=1,stop_confirmed=True)
            else:
                raw=prompt.document(prompt.row(success=False,error='synthetic-provider-error',gah=attempt['expected_binding']))
                result=self.normalize(adapter,raw,attempt['expected_binding'])
            self.assertIsNone(result['observation']);self.assertEqual(result['error_class'],'EXECUTION_FAILURE')
            with self.open(adapter+'-error') as store:
                store.start_run(self.bound,self.profile)
                self.assertTrue(store.record_attempt({**attempt,'result':result})['accepted'])
                terminal=store.finalize('run-1')
                self.assertIn(terminal['decision']['assurance'],('HOLD','UNKNOWN'))
                self.assertFalse(terminal['ci_eligible'])


if __name__=='__main__':unittest.main()
