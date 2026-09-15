"""保存済みFindingの状態照会・着手・再検証要求・独立確認を行う。"""
import argparse
import json
from pathlib import Path
import re
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gah.contracts import MAX_DOCUMENT_BYTES, decode_document
from gah.finding_lifecycle import validate_request, _reference
from gah.supervisor_checkpoint import _plain_directory
from gah.docker_runner import operation_lock
from tools.authority_runtime import AuthorityRuntime


def execute(runtime, request):
    request=validate_request(request)
    uid=12001 if request['action']=='finding_dispose' else (12003 if request['action']=='finding_confirm' else 12004)
    result=runtime.client(uid,request)
    if result.get('kind')=='authority_error':
        reason=result.get('reason')
        if type(reason) is not str or not re.fullmatch('[A-Z][A-Z0-9_]{0,63}',reason):
            raise ValueError('RESPONSE_INVALID')
        return {'schema_version':1,'kind':'finding_management_error','reason':reason,'ci_eligible':False},2
    if (type(result.get('schema_version')) is not int or result.get('schema_version')!=1 or result.get('kind')!='evaluation_authority_result'
            or result.get('action')!=request['action'] or result.get('request_id')!=request['request_id']
            or result.get('ci_eligible') is not False or type(result.get('state')) is not dict
            or result['state'].get('finding_ref')!=request['finding_ref']
            or result.get('state_ref')!=_reference(result['state'])):
        raise ValueError('RESPONSE_INVALID')
    return result,0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',required=True)
    parser.add_argument('--request',required=True)
    args=parser.parse_args()
    try:
        folder=_plain_directory(Path(args.runtime))
        if not folder.is_relative_to(ROOT) or not (folder/'deployment.json').is_file():
            raise ValueError('EXISTING_RUNTIME_REQUIRED')
        with Path(args.request).open('rb') as stream:
            request=validate_request(decode_document(stream.read(MAX_DOCUMENT_BYTES+1)))
        with operation_lock(folder/'supervised-transport','deployment','supervisor'):
            result,code=execute(AuthorityRuntime(folder),request)
    except (Exception,KeyboardInterrupt):
        result={'schema_version':1,'kind':'finding_management_error','reason':'MANAGEMENT_INCOMPLETE','ci_eligible':False};code=2
    sys.stdout.write(json.dumps(result,ensure_ascii=False,sort_keys=True)+'\n')
    return code


if __name__=='__main__':raise SystemExit(main())
