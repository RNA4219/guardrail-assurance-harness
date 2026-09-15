"""基準管理主体が採択済み対象を廃止し、完全参照を返す。"""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah.contracts import MAX_DOCUMENT_BYTES,decode_document
from gah.target_retirement import validate_request,KIND,_identifier
from gah.run_contracts import content_ref
from gah.supervisor_checkpoint import _plain_directory
from gah.docker_runner import operation_lock
from tools.authority_runtime import AuthorityRuntime


def execute(runtime,request):
    request=validate_request(request)
    result=runtime.client(12001,request)
    if type(result) is not dict:raise ValueError('RESPONSE_INVALID')
    if result.get('kind')=='authority_error':
        return {'schema_version':1,'kind':'target_management_error','reason':'RETIREMENT_REJECTED','ci_eligible':False},2
    value=result.get('retirement')
    if (type(result.get('schema_version')) is not int or result['schema_version']!=1
            or result.get('kind')!='evaluation_authority_result' or result.get('action')!='target_retire'
            or result.get('request_id')!=request['request_id'] or result.get('ci_eligible') is not False
            or type(value) is not dict or value.get('request')!=request or value.get('actor_id')!='manager'
            or value.get('context')!='manager-context'
            or result.get('retirement_ref')!=content_ref(KIND,_identifier(request['target_ref']),value)):
        raise ValueError('RESPONSE_INVALID')
    return result,0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True);parser.add_argument('--request',required=True)
    args=parser.parse_args()
    try:
        folder=_plain_directory(Path(args.runtime))
        if not folder.is_relative_to(ROOT) or not (folder/'deployment.json').is_file():raise ValueError('EXISTING_RUNTIME_REQUIRED')
        with Path(args.request).open('rb') as stream:request=validate_request(decode_document(stream.read(MAX_DOCUMENT_BYTES+1)))
        with operation_lock(folder/'supervised-transport','deployment','supervisor'):
            result,code=execute(AuthorityRuntime(folder),request)
    except (Exception,KeyboardInterrupt):
        result={'schema_version':1,'kind':'target_management_error','reason':'RETIREMENT_INCOMPLETE','ci_eligible':False};code=2
    sys.stdout.write(json.dumps(result,ensure_ascii=False,sort_keys=True)+'\n');return code


if __name__=='__main__':raise SystemExit(main())
