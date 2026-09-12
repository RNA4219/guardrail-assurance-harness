"""固定UC-CIの通常runを開始・再開・取消し・照会する。"""
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
from tools.authority_runtime import AuthorityRuntime


def execute(runtime, runner, folder, request, mode, *, clock=None, hook=None):
    request = validate_input(request)
    kwargs = {} if clock is None else {'clock': clock}
    checkpoint = Checkpoint(folder/'checkpoints')
    # 同一runの排他はactor認証とは別。共有deploymentのtransportも直列化する。
    with operation_lock(folder/'execution.sqlite', request['run_id'], 'supervisor'):
        checkpoint.put('source-lock', {'sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
            for name in ('tools/gah_run.py', 'src/gah/supervised_run.py', 'src/gah/supervisor_checkpoint.py',
                         'src/gah/docker_runner.py', 'src/gah/execution_journal.py')}})
        return Supervisor(runtime, runner, checkpoint, request, hook=hook, **kwargs).execute(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('run','resume','cancel','status'))
    parser.add_argument('--runtime', required=True, help='既存deployment.jsonのあるrepo内ディレクトリ')
    parser.add_argument('--request', required=True, help='契約完全参照とrun IDを指定するJSON')
    args = parser.parse_args()
    try:
        folder = _plain_directory(Path(args.runtime))
        if not folder.is_relative_to(ROOT) or not (folder/'deployment.json').is_file():
            raise ValueError('EXISTING_RUNTIME_REQUIRED')
        with Path(args.request).open('rb') as stream:
            request = validate_input(decode_document(stream.read(MAX_DOCUMENT_BYTES+1)))
        run_folder = _plain_directory(folder/'supervised'/hashlib.sha256(request['run_id'].encode()).hexdigest())
        if args.mode != 'run' and not run_folder.is_dir():
            raise ValueError('CHECKPOINT_REQUIRED')
        run_folder.mkdir(parents=True, exist_ok=True)
        with operation_lock(folder/'supervised-transport', 'deployment', 'supervisor'):
            runtime = AuthorityRuntime(folder)
            runner = DockerRunner(ROOT/'config/fixture-runtime.lock.json', run_folder/'execution.sqlite')
            result = execute(runtime, runner, run_folder, request, args.mode)
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
