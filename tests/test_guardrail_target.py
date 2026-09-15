"""独立した合成対象を400入力で評価する。学習済みモデルの性能試験ではない。"""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah.evaluation_data import build_pack
from gah.wire import canonical_bytes
spec=importlib.util.spec_from_file_location('synthetic_guardrail_target',ROOT/'fixtures/llm/guardrail_target.py')
target=importlib.util.module_from_spec(spec);spec.loader.exec_module(target)


class GuardrailTargetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pack=build_pack()
        cls.documents={canonical_bytes(item['ref']):item['document'] for item in cls.pack['documents']}
        first=cls.pack['case_sets']['acceptance']['cases'][0]
        cls.example=cls.documents[canonical_bytes(first['session_steps'][-1]['input_ref'])]

    def test_two_frozen_versions_measure_required_finite_corpus_changes(self):
        expected={'baseline-v1':{'TP':196,'FN':4,'FP':4,'TN':196},'degraded-v2':{'TP':184,'FN':16,'FP':2,'TN':198}}
        for version,want in expected.items():
            counts={key:0 for key in want};stages=0
            for case in self.pack['case_sets']['acceptance']['cases']:
                for stage in case['session_steps']:
                    result=target.evaluate(deepcopy(self.documents[canonical_bytes(stage['input_ref'])]),version)
                    stages+=1
                    self.assertEqual(set(result),{'detection','action'})
                    if stage['stage_id']==case['scored_stage_id']:
                        observed=result['detection']=='detect'
                        key=('TP' if observed else 'FN') if case['expected_label']=='positive' else ('FP' if observed else 'TN')
                        counts[key]+=1
            self.assertEqual(stages,600)
            self.assertEqual(counts,want)

    def test_all_stages_use_distinct_synthetic_identity_and_observed_memory_effects(self):
        from gah.llm_evaluator import SyntheticEvaluator,SYNTHETIC_MODEL,MODEL,parse_response
        evaluator=SyntheticEvaluator(self.pack,model=SYNTHETIC_MODEL)
        checked=0
        for case in self.pack['case_sets']['acceptance']['cases']:
            session=evaluator.open_case('acceptance',case['case_id'])
            for index,stage in enumerate(case['session_steps']):
                prepared=session.prepare()
                self.assertEqual(prepared['request']['model'],SYNTHETIC_MODEL)
                if index==0:self.assertEqual(len(prepared['request']['messages']),2)
                document=json.loads(prepared['request']['messages'][-1]['content'])
                selection=target.evaluate(document,'baseline-v1')
                raw=canonical_bytes({'model':SYNTHETIC_MODEL,'choices':[{'index':0,'finish_reason':'stop',
                    'message':{'role':'assistant','content':json.dumps(selection)}}],
                    'usage':{'prompt_tokens':0,'completion_tokens':0,'total_tokens':0}})
                result=session.accept(raw)
                self.assertEqual(result['status'],'COMPLETE')
                self.assertEqual(result['effect']['applied'],selection['action']=='apply')
                if index==0:self.assertEqual(result['effect']['counter_before'],0)
                checked+=1
            self.assertTrue(session.snapshot()['complete'])
        self.assertEqual(checked,600)
        self.assertEqual(parse_response(raw)['reason_code'],'MODEL_MISMATCH')
        self.assertNotEqual(SYNTHETIC_MODEL,MODEL)

    def test_scope_text_labels_and_unbounded_features_are_not_target_inputs(self):
        for mutate in (lambda x:x.update(expected_label='negative'),lambda x:x.update(command='synthetic marker'),
                       lambda x:x.update(schema_version=True),lambda x:x.update(required=[]),
                       lambda x:x['observed'].update(extra=True),lambda x:x['observed'].update(local_destination=1)):
            value=deepcopy(self.example);mutate(value)
            with self.assertRaises(ValueError):target.evaluate(value,'baseline-v1')
        with self.assertRaises(ValueError):target.evaluate(self.example,'unknown-version')

    def test_unknown_observation_is_not_invented_as_an_allow(self):
        value=deepcopy(self.example)
        value['observed']={name:True for name in value['observed']}
        value['observed'][value['required'][0]]=None
        for version in target.VERSIONS:
            self.assertEqual(target.evaluate(value,version),{'detection':'indeterminate','action':'defer'})

    def test_stdio_target_process_reads_only_input_and_returns_only_finite_selection(self):
        argv=[sys.executable,'-I','-B',str(ROOT/'fixtures/llm/guardrail_target.py')]
        request={'version':'baseline-v1','input':self.example}
        result=subprocess.run(argv,input=canonical_bytes(request),capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0);self.assertFalse(result.stderr)
        self.assertEqual(json.loads(result.stdout),target.evaluate(self.example,'baseline-v1'))
        invalid=subprocess.run(argv,input=b'{"version":"baseline-v1","version":"degraded-v2","input":{}}',capture_output=True,timeout=10)
        self.assertEqual(invalid.returncode,2);self.assertEqual(json.loads(invalid.stdout),{'error':'TARGET_INPUT_INVALID'})


if __name__=='__main__':unittest.main()
