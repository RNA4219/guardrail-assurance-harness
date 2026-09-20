"""固定UC-CI / UC-LLMの通常runを開始・再開・取消し・照会する。"""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from gah.contracts import MAX_DOCUMENT_BYTES, decode_document
from gah.docker_runner import DockerRunner, operation_lock
from gah.supervisor_checkpoint import Checkpoint, _plain_directory
from gah.supervised_run import Supervisor, validate_input
from gah.llm_supervised_run import LlmSupervisor
from gah.supervised_capacity import capacity_reason, failure_result, prepare_sink
from tools.authority_runtime import AuthorityRuntime


def execute(runtime, runner, folder, request, mode, *, clock=None, hook=None):
    request = validate_input(request)
    if clock is None:
        clock = getattr(runtime, 'clock', None)
    kwargs = {} if clock is None else {'clock': clock}
    # 同一runの排他はactor認証とは別。共有deploymentのtransportも直列化する。
    with operation_lock(folder/'execution.sqlite', request['run_id'], 'supervisor'):
        try:
            sink = prepare_sink(folder, request['run_id'], mode)
        except Exception as error:
            if mode not in ('cancel', 'status'):
                return failure_result(None, request['run_id'],
                                      capacity_reason(error) or 'SUPERVISION_INCOMPLETE', 'SINK_INIT')
            # 記録先の障害だけで取消し・現在状態の照会を妨げない。
            sink = None
        stage = 'CHECKPOINT_INIT'
        try:
            checkpoint = Checkpoint(folder/'checkpoints')
            stage = 'SOURCE_LOCK'
            checkpoint.put('source-lock', {'sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
                for name in ('tools/gah_run.py', 'src/gah/supervised_run.py', 'src/gah/supervisor_checkpoint.py',
                             'src/gah/docker_runner.py', 'src/gah/execution_journal.py', 'src/gah/run_scope.py',
                             'src/gah/llm_supervised_run.py', 'src/gah/guardrail_runner.py', 'src/gah/guardrail_results.py',
                             'src/gah/sqlite_limits.py', 'src/gah/bounded_files.py', 'src/gah/storage_budget.py',
                             'src/gah/worker_metrics.py', 'src/gah/supervised_capacity.py',
                             'src/gah/partitioned_llm_admission.py', 'src/gah/partitioned_llm_materialization.py',
                             'src/gah/partitioned_guardrail_results.py', 'src/gah/partitioned_trial_plan.py',
                             'src/gah/partitioned_scale_corpus.py', 'src/gah/partitioned_case_set.py',
                             'src/gah/query_scale_data.py', 'src/gah/partitioned_run_contracts.py',
                             'src/gah/partitioned_llm_transitions.py', 'src/gah/run_contracts.py',
                             'src/gah/execution_profiles.py', 'src/gah/llm_materialization.py')}})
            stage = 'SUPERVISOR_INIT'
            supervisor = LlmSupervisor if getattr(runner, 'execution_kind', None) == 'guardrail' else Supervisor
            instance = supervisor(runtime, runner, checkpoint, request, hook=hook, **kwargs)
            stage = 'EXECUTE'
            return instance.execute(mode)
        except Exception as error:
            reason = capacity_reason(error)
            if reason is None:
                raise
            return failure_result(sink, request['run_id'], reason, stage)



def validate_diagnostic_input(value):
    """明示v6の無害な固定fixture診断だけを受け付ける。"""
    from gah.contracts import require_object, require_id
    from gah.partitioned_run_store import validate_request
    from gah.wire import canonical_bytes
    require_object(value, {'schema_version', 'kind', 'begin_request', 'selector'})
    if (type(value['schema_version']) is not int or value['schema_version'] != 1
            or value['kind'] != 'partitioned_diagnostic_request'):
        raise ValueError('INVALID_DIAGNOSTIC_REQUEST')
    begin = validate_request(value['begin_request'])
    if begin['action'] != 'run_begin_v2':
        raise ValueError('INVALID_DIAGNOSTIC_REQUEST')
    manifest = begin['manifest']
    if (manifest['purpose'] != 'diagnostic' or manifest['baseline_ref'] is not None
            or manifest['use_cases'] != ['UC-CI']):
        raise ValueError('DIAGNOSTIC_SCOPE_UNSUPPORTED')
    selector = value['selector']
    require_object(selector, {'obligation_id', 'case_id', 'trial_id', 'variant'})
    for name in selector:
        require_id(selector[name])
    if selector['variant'] != 'candidate':
        raise ValueError('DIAGNOSTIC_SCOPE_UNSUPPORTED')
    return decode_document(canonical_bytes({
        'schema_version': 1, 'kind': 'partitioned_diagnostic_request',
        'begin_request': begin, 'selector': selector}))


def execute_diagnostic(runtime, runner, folder, request, *, clock=None):
    """開始応答を保存し、v6一件診断へ渡す。通常runやCIへ変換しない。"""
    import re
    from gah.partitioned_diagnostic_run import (
        execute_one_entry, _validate_begin_response, _run_view, _operation_id)
    flags = {name: False for name in ('ci_eligible', 'authority_connected',
        'resource_closure_verified', 'baseline_freshness_verified',
        'adoption_verified', 'admission_verified')}
    run_id = None
    try:
        request = validate_diagnostic_input(request)
        begin_request = request['begin_request']
        manifest = begin_request['manifest']
        run_id = manifest['run_id']
        if type(runner) is not DockerRunner or getattr(runtime, 'database_mode', None) != 'partitioned-v6':
            raise ValueError('FIXED_V6_RUNTIME_REQUIRED')
        folder = _plain_directory(folder)
        with operation_lock(folder/'execution.sqlite', run_id, 'diagnostic-begin'):
            checkpoint = Checkpoint(folder/'diagnostic-launch')
            checkpoint.put('request', request)
            saved = checkpoint.get('begin-response')
            if saved is None:
                response = runtime.client(12004, begin_request)
                if type(response) is dict and response.get('kind') == 'authority_error':
                    raise ValueError(response.get('reason', 'AUTHORITY_UNAVAILABLE'))
            else:
                if type(saved) is not dict or set(saved) != {'response'}:
                    raise ValueError('BEGIN_CHECKPOINT_INVALID')
                response = saved['response']
            if type(response) is dict and response.get('request_id') != begin_request['request_id']:
                raise ValueError('BEGIN_RESPONSE_MISMATCH')
            _validate_begin_response(response, manifest, begin_request)
            _, profile = _run_view(response, manifest)
            if (response['request_id'] != begin_request['request_id']
                    or profile != begin_request['execution_profile']):
                raise ValueError('BEGIN_RESPONSE_MISMATCH')
            if saved is None:
                checkpoint.put('begin-response', {'response': response})
        kwargs = {} if clock is None else {'clock': clock}
        result = execute_one_entry(runtime, runner, folder, response, manifest,
                                   request['selector'], begin_request=begin_request, **kwargs)
        operation_id = _operation_id(manifest, request['selector'])
        result_fields = set(flags) | {'schema_version', 'kind', 'run_id', 'operation_id',
                                     'attempt_id', 'execution_status', 'resource_closed', 'diagnostic_finalized'}
        if (type(result) is not dict or set(result) != result_fields
                or type(result.get('schema_version')) is not int or result['schema_version'] != 1
                or result.get('kind') != 'partitioned_diagnostic_entry_result'
                or result.get('operation_id') != operation_id
                or result.get('attempt_id') != operation_id + '-attempt'
                or result.get('execution_status') != 'COMPLETED'
                or result.get('run_id') != run_id or result.get('resource_closed') is not True
                or result.get('diagnostic_finalized') is not False
                or any(result.get(name) is not False for name in flags)):
            raise ValueError('DIAGNOSTIC_RESULT_INVALID')
        return {**result, 'exit_code': 0}
    except Exception as error:
        reason = getattr(error, 'code', str(error))
        if type(reason) is not str or re.fullmatch(r'[A-Z][A-Z0-9_]{0,95}', reason) is None:
            reason = capacity_reason(error) or 'DIAGNOSTIC_INCOMPLETE'
        return {'schema_version': 1, 'kind': 'partitioned_diagnostic_entry_error',
                'run_id': run_id, 'reason': reason, 'exit_code': 2, **flags}


def list_runs(runtime, request, stream):
    """認証されたmetadata一覧。通常runの開始やCI許可を行わない。"""
    from gah import run_catalog
    try:
        request=run_catalog.validate_request(request)
        value=runtime.client(12004,request)
        if type(value) is dict and value.get("kind")=="authority_error":
            reason=value.get("reason")
            if reason in {"STALE_OR_INVALIDATED","AUTHORITY_DENIED","AUTHORITY_REVOKED","CONTRACT_MISSING","CAPACITY_EXCEEDED"}:
                raise ValueError(reason)
        value=run_catalog.validate_response(request,value)
        stream.write(json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
        stream.flush()
        return 0
    except Exception as error:
        reason=getattr(error,"code",str(error))
        rejected={"INVALID_REQUEST","STALE_OR_INVALIDATED","AUTHORITY_DENIED","AUTHORITY_REVOKED","CONTRACT_MISSING","CAPACITY_EXCEEDED"}
        code=1 if reason in rejected else 2
        if reason not in rejected:
            reason="CATALOG_UNAVAILABLE"
        try:
            stream.write(json.dumps({"schema_version":1,"kind":"run_catalog_error","reason":reason,
                                     "ci_eligible":False,"exit_code":code})+'\n')
            stream.flush()
        except Exception:
            pass
        return code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run','resume','cancel','status','list','diagnostic'))
    parser.add_argument('--runtime', help='既存deployment.jsonのあるrepo内ディレクトリ')
    parser.add_argument('--request', help='契約完全参照とrun IDを指定するJSON')
    parser.add_argument('--runner', choices=('fixture', 'guardrail'), default=None, help='採択済み契約に対応する固定実行器')
    parser.add_argument('--setup-plan', help='完了済みsetupの計画からruntime/requestを解決')
    args = parser.parse_args()
    try:
        if args.setup_plan is not None:
            if args.runtime is not None or args.request is not None or args.mode in ('list', 'diagnostic'):
                raise ValueError('SETUP_ARGUMENT_CONFLICT')
            from tools.setup_request import resolve
            paths = resolve(args.setup_plan)
            if args.runner is not None and args.runner != paths['runner']:
                raise ValueError('SETUP_ARGUMENT_CONFLICT')
            args.runtime, args.request, args.runner = paths['runtime'], paths['run_request'], paths['runner']
        elif args.runtime is None or args.request is None:
            raise ValueError('RUNTIME_AND_REQUEST_REQUIRED')
        args.runner = args.runner or 'fixture'
        folder = _plain_directory(Path(args.runtime))
        if not folder.is_relative_to(ROOT) or not (folder/'deployment.json').is_file():
            raise ValueError('EXISTING_RUNTIME_REQUIRED')
        with Path(args.request).open('rb') as stream:
            raw_request = decode_document(stream.read(MAX_DOCUMENT_BYTES+1))
        if args.mode == 'list':
            with operation_lock(folder/'supervised-transport','deployment','supervisor'):
                runtime=AuthorityRuntime(folder,reuse_clients=True,keep_clients_running=True)
                try:
                    return list_runs(runtime,raw_request,sys.stdout)
                finally:
                    runtime.close_clients()
        if args.mode == 'diagnostic':
            if args.runner != 'fixture':
                raise ValueError('FIXED_RUNNER_REQUIRED')
            request = validate_diagnostic_input(raw_request)
            run_id = request['begin_request']['manifest']['run_id']
            run_folder = _plain_directory(folder/'partitioned-diagnostic'/hashlib.sha256(run_id.encode()).hexdigest())
        else:
            request = validate_input(raw_request)
            run_folder = _plain_directory(folder/'supervised'/hashlib.sha256(request['run_id'].encode()).hexdigest())
        if args.mode not in ('run', 'diagnostic') and not run_folder.is_dir():
            raise ValueError('CHECKPOINT_REQUIRED')
        run_folder.mkdir(parents=True, exist_ok=True)
        with operation_lock(folder/'supervised-transport', 'deployment', 'supervisor'):
            runtime_kwargs = {'database_mode': 'partitioned-v6'} if args.mode == 'diagnostic' else {}
            runtime = AuthorityRuntime(folder, reuse_clients=True, keep_clients_running=True, **runtime_kwargs)
            try:
                if args.runner == 'guardrail':
                    from gah.guardrail_runner import GuardrailRunner
                    runner = GuardrailRunner(run_folder/'execution.sqlite')
                else:
                    runner = DockerRunner(ROOT/'config/fixture-runtime.lock.json', run_folder/'execution.sqlite')
                if args.mode == 'diagnostic':
                    result = execute_diagnostic(runtime, runner, run_folder, request, clock=runtime.clock)
                else:
                    result = execute(runtime, runner, run_folder, request, args.mode, clock=runtime.clock)
            finally:
                runtime.close_clients()
        sys.stdout.write(json.dumps(result,ensure_ascii=False,sort_keys=True,allow_nan=False)+'\n')
        sys.stdout.flush()
        return result['exit_code']
    except (Exception, KeyboardInterrupt):
        try:
            sys.stdout.write(json.dumps({'schema_version':1,'kind':'supervised_run_error',
                'reason':'SUPERVISION_INCOMPLETE','ci_eligible':False,'exit_code':2})+'\n')
            sys.stdout.flush()
        except Exception:
            pass
        return 2


if __name__=='__main__':
    raise SystemExit(main())
