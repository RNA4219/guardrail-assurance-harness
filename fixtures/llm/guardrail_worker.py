"""固定guardrailの1ケースを独立したメモリ状態で実行する隔離worker。"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'src'))
from gah.contracts import decode_document, require_object, require_digest
from gah.normalized import validate_binding
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
spec=importlib.util.spec_from_file_location('fixed_target',ROOT/'guardrail_target.py')
target=importlib.util.module_from_spec(spec);spec.loader.exec_module(target)


def evaluate(request, *, clock=time.time_ns):
    require_object(request,{'schema_version','kind','target','stages'})
    if type(request['schema_version']) is not int or request['schema_version']!=1 or request['kind']!='guardrail_case_request':
        raise ValueError('CASE_REQUEST_INVALID')
    document=request['target']
    require_object(document,{'schema_version','kind','target_id','behavior_version','source_digest','runtime_image_id','trained_model'})
    if (type(document['schema_version']) is not int or document['schema_version']!=1
            or document['kind']!='synthetic_guardrail_target' or document['target_id']!='synthetic-guardrail'
            or document['trained_model'] is not False or type(document['behavior_version']) is not str
            or document['behavior_version'] not in target.VERSIONS
            or document['source_digest']!=hashlib.sha256((ROOT/'guardrail_target.py').read_bytes()).hexdigest()
            or type(document['runtime_image_id']) is not str or not document['runtime_image_id'].startswith('sha256:')):
        raise ValueError('TARGET_INVALID')
    require_digest(document['runtime_image_id'][7:])
    ref=content_ref('target',document['target_id'],document)
    stages=request['stages']
    if type(stages) is not list or not 1<=len(stages)<=2:raise ValueError('STAGES_INVALID')
    fixture_digest=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    checked=[];common=None;ids=set()
    for stage in stages:
        require_object(stage,{'binding','input'})
        binding=validate_binding(stage['binding'])
        if binding['fixture_digest']!=fixture_digest or binding['target_digest']!=ref['digest'] or binding['stage_id'] in ids:
            raise ValueError('BINDING_INVALID')
        identity={key:value for key,value in binding.items() if key!='stage_id'}
        if common is not None and common!=identity:raise ValueError('CASE_ISOLATION_INVALID')
        common=identity;ids.add(binding['stage_id'])
        selection=target.evaluate(stage['input'],document['behavior_version'])
        checked.append((binding,stage['input'],selection))
    counter=0;results=[];effects=[];timings=[];previous_finished=None
    for binding,input_doc,_ in checked:
        started_ns=clock()
        if type(started_ns) is not int or started_ns<0 or previous_finished is not None and started_ns<previous_finished:
            raise ValueError('STAGE_CLOCK_INVALID')
        selection=target.evaluate(input_doc,document['behavior_version'])
        before=counter
        if selection['action']=='apply':counter+=1
        applied=counter==before+1
        required=[input_doc['observed'][key] for key in input_doc['required']]
        expected_violation=any(value is False for value in required)
        unknown=not expected_violation and any(value is None for value in required)
        results.append({'schema_version':1,'kind':'gah_generic_result','binding':binding,'mode':'llm',
            'observations':{'detection':selection['detection'],'deviation':None if unknown else applied and expected_violation}})
        effects.append({'stage_id':binding['stage_id'],'counter_before':before,'counter_after':counter,'applied':applied})
        finished_ns=clock()
        if type(finished_ns) is not int or finished_ns<started_ns:
            raise ValueError('STAGE_CLOCK_INVALID')
        timings.append({'stage_id':binding['stage_id'],'started_at':started_ns//1_000_000_000,'finished_at':finished_ns//1_000_000_000})
        previous_finished=finished_ns
    return {'schema_version':1,'kind':'guardrail_case_result','results':results,'effects':effects,'stage_timings':timings,
        'initial_counter':0,'final_counter':counter,'synthetic_target':True,'trained_model':False,
        'usage':{'input_tokens':0,'output_tokens':0,'cost_usd':'0'}}


def main():
    try:
        raw = sys.stdin.buffer.read(65537)
        if len(raw) > 65536:
            raise ValueError("CASE_SIZE")
        result=evaluate(decode_document(raw))
        sys.stdout.buffer.write(canonical_bytes(result)+b'\n')
        return 0
    except (Exception,KeyboardInterrupt):
        sys.stdout.write('{"schema_version":1,"kind":"guardrail_worker_error","reason":"CASE_INVALID"}\n')
        return 2


if __name__=='__main__':raise SystemExit(main())
