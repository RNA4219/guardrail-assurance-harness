"""未成立Mutationの独立審査・承認・現在照会を行う。CI利用は許可しない。"""
import argparse
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from gah.contracts import MAX_DOCUMENT_BYTES,MAX_INTEGER,decode_document,require_ref
from gah.mutation_reviews import validate_request,VALIDATION,APPROVAL,POLICY_REF
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes
from gah.supervisor_checkpoint import _plain_directory
from gah.docker_runner import operation_lock
from tools.authority_runtime import AuthorityRuntime


def _record(value,ref,kind,evidence):
    require_ref(ref)
    if (type(value) is not dict or ref['kind']!=kind or type(value.get('schema_version')) is not int
            or value['schema_version']!=1 or value.get('kind')!=kind or value.get('ci_eligible') is not False
            or value.get('request',{}).get('expected_evidence_ref')!=evidence
            or content_ref(kind,value['review_id'],value)!=ref):raise ValueError('RESPONSE_INVALID')


def execute(runtime,request):
    request=validate_request(request);action=request['action']
    uid={'mutation_review_validate':12003,'mutation_review_approve':12001,'mutation_review_current':12004}[action]
    result=runtime.client(uid,request)
    if type(result) is not dict:raise ValueError('RESPONSE_INVALID')
    if result.get('kind')=='authority_error':
        reason=result.get('reason')
        if (result.get('ci_eligible') is not False or type(reason) is not str
                or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',reason)):raise ValueError('RESPONSE_INVALID')
        return {'schema_version':1,'kind':'mutation_review_error','reason':reason,'ci_eligible':False},2
    base={'schema_version','kind','action','request_id','ci_eligible'}
    specific=({'validation_ref','validation'} if action=='mutation_review_validate' else
        {'approval_ref','approval'} if action=='mutation_review_approve' else
        {'run_id','evidence_ref','contract_ref','policy_ref','checked_at','items','counts','original_decision_ref',
            'original_decision_unchanged','required_obligations_unchanged'})
    if (set(result)!=base|specific or type(result['schema_version']) is not int or result['schema_version']!=1
            or result['kind']!='evaluation_authority_result' or result['action']!=action
            or result['request_id']!=request['request_id'] or result['ci_eligible'] is not False):raise ValueError('RESPONSE_INVALID')
    evidence=request['expected_evidence_ref']
    if action in ('mutation_review_validate','mutation_review_approve'):
        field,kind=('validation',VALIDATION) if action=='mutation_review_validate' else ('approval',APPROVAL)
        _record(result[field],result[field+'_ref'],kind,evidence)
        if result[field]['request']!=request:raise ValueError('RESPONSE_INVALID')
        if field=='approval' and result[field]['validation_ref']!=request['validation_ref']:raise ValueError('RESPONSE_INVALID')
        if field=='validation':
            proof=result[field]['proof']
            if (proof['evidence_ref']!=evidence or proof['policy_ref']!=POLICY_REF
                    or proof['attempt_ref']['id']!=request['attempt_id']):raise ValueError('RESPONSE_INVALID')
    else:
        if (result['run_id']!=request['run_id'] or result['evidence_ref']!=evidence or result['policy_ref']!=POLICY_REF
                or result['original_decision_unchanged'] is not True or result['required_obligations_unchanged'] is not True
                or type(result['checked_at']) is not int or not 0<=result['checked_at']<=MAX_INTEGER
                or type(result['items']) is not list or len(result['items'])>1000):raise ValueError('RESPONSE_INVALID')
        for field,kind in [('contract_ref','evaluation_contract'),('original_decision_ref','run_decision')]:
            require_ref(result[field])
            if result[field]['kind']!=kind:raise ValueError('RESPONSE_INVALID')
        counts={v:{'approved_exclusions':0,'pending_exclusions':0} for v in ('baseline','candidate')};seen=set()
        for item in result['items']:
            if type(item) is not dict or set(item)!={'validation_ref','approval_ref','validation','approval','status'}:raise ValueError('RESPONSE_INVALID')
            _record(item['validation'],item['validation_ref'],VALIDATION,evidence)
            proof=item['validation']['proof'];identifier=item['validation_ref']['id']
            if identifier in seen or proof['contract_ref']!=result['contract_ref'] or proof['evidence_ref']!=evidence:raise ValueError('RESPONSE_INVALID')
            seen.add(identifier)
            if item['approval'] is None:
                if item['approval_ref'] is not None or item['status']!='EXCLUSION_PENDING':raise ValueError('RESPONSE_INVALID')
            else:
                _record(item['approval'],item['approval_ref'],APPROVAL,evidence)
                if item['approval']['validation_ref']!=item['validation_ref']:raise ValueError('RESPONSE_INVALID')
            if item['status'] not in ('EXCLUDED','EXCLUSION_PENDING') or proof['variant'] not in counts:raise ValueError('RESPONSE_INVALID')
            counts[proof['variant']]['approved_exclusions' if item['status']=='EXCLUDED' else 'pending_exclusions']+=1
        if (type(result['counts']) is not dict or result['counts']!=counts
                or any(type(value) is not int for row in result['counts'].values() for value in row.values())):raise ValueError('RESPONSE_INVALID')
    if len(canonical_bytes(result))>MAX_DOCUMENT_BYTES:raise ValueError('RESPONSE_INVALID')
    return result,0


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--runtime',required=True);parser.add_argument('--request',required=True)
    args=parser.parse_args()
    try:
        folder=_plain_directory(Path(args.runtime))
        if not folder.is_relative_to(ROOT) or not (folder/'deployment.json').is_file():raise ValueError('EXISTING_RUNTIME_REQUIRED')
        with Path(args.request).open('rb') as stream:request=validate_request(decode_document(stream.read(MAX_DOCUMENT_BYTES+1)))
        with operation_lock(folder/'supervised-transport','deployment','supervisor'):result,code=execute(AuthorityRuntime(folder),request)
    except (Exception,KeyboardInterrupt):
        result={'schema_version':1,'kind':'mutation_review_error','reason':'REVIEW_UNAVAILABLE','ci_eligible':False};code=2
    try:sys.stdout.write(json.dumps(result,ensure_ascii=False,sort_keys=True)+'\n');sys.stdout.flush()
    except (OSError,UnicodeError):return 2
    return code


if __name__=='__main__':raise SystemExit(main())
