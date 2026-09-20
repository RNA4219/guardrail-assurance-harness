"""自作のguardrailと固定評価workerを不変のDocker imageへ配置する。"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from tools.prepare_fixture_runtime import BASE

ROOT=Path(__file__).resolve().parents[1]
SOURCES=['fixtures/llm/guardrail_worker.py','fixtures/llm/guardrail_target.py','src/gah/__init__.py',
    'src/gah/contracts.py','src/gah/normalized.py','src/gah/run_contracts.py','src/gah/wire.py',
    'src/gah/policy.py','src/gah/registry.py','src/gah/corpus.py']
ENTRYPOINT=['/usr/local/bin/python','-I','-B','/opt/gah/guardrail_worker.py']


def main():
    docker=shutil.which('docker')
    if not docker:raise SystemExit('DOCKER_UNAVAILABLE')
    prefix=[docker,'--host','npipe:////./pipe/dockerDesktopLinuxEngine' if os.name=='nt' else 'unix:///var/run/docker.sock']
    hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in SOURCES}
    digest=hashlib.sha256(json.dumps(hashes,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    parent=ROOT/'.ga'/'guardrail-build'
    parent.mkdir(parents=True, exist_ok=True)
    context=Path(tempfile.mkdtemp(prefix=digest[:16]+'-', dir=parent))
    copies=[]
    for name in SOURCES:
        source=ROOT/name
        relative=Path(name).name if name.startswith('fixtures/') else name
        destination=context/relative;destination.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,destination)
        if hashlib.sha256(destination.read_bytes()).hexdigest()!=hashes[name]:raise SystemExit('COPY_CHANGED')
        copies.append('COPY '+relative+' /opt/gah/'+relative)
    dockerfile='FROM '+BASE+'\n'+'\n'.join(copies)+'\nLABEL org.gah.guardrail.sources='+digest+'\nENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONHASHSEED=0\nUSER 65532:65532\nWORKDIR /work\nENTRYPOINT '+json.dumps(ENTRYPOINT)+'\nCMD []\n'
    (context/'Dockerfile').write_text(dockerfile,encoding='utf-8')
    tag='gah-guardrail-fixture:'+digest[:16]
    subprocess.run(prefix+['build','--platform=linux/amd64','--network=none','--pull=false','--tag',tag,str(context)],cwd=ROOT,check=True,timeout=180)
    raw=subprocess.check_output(prefix+['image','inspect',tag],timeout=20)
    if len(raw)>131072:raise SystemExit('IMAGE_INVALID')
    image=json.loads(raw)[0]
    if any(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=value for name,value in hashes.items()):raise SystemExit('SOURCE_CHANGED')
    if (image['Os']!='linux' or image['Architecture']!='amd64'
            or image['Config']['Entrypoint']!=ENTRYPOINT or image['Config']['User']!='65532:65532'
            or image['Config']['WorkingDir']!='/work' or image['Config'].get('Cmd')
            or image['Config'].get('Volumes') or image['Config'].get('ExposedPorts')
            or image['Config']['Labels']['org.gah.guardrail.sources']!=digest):raise SystemExit('IMAGE_INVALID')
    lock={'schema_version':1,'kind':'guardrail_runtime_lock','image_id':image['Id'],'base_ref':BASE,
        'worker_digest':hashes[SOURCES[0]],'target_source_digest':hashes[SOURCES[1]],'sources_digest':digest,'source_sha256':hashes,
        'docker_binary_digest':hashlib.sha256(Path(docker).read_bytes()).hexdigest(),
        'entrypoint':ENTRYPOINT,'environment':image['Config']['Env'],'platform':'linux/amd64'}
    destination=ROOT/'config/guardrail-runtime.lock.json'
    previous=destination.read_bytes() if destination.exists() else None
    if previous is not None:
        archive=ROOT/'.ga'/'guardrail-locks'
        archive.mkdir(parents=True, exist_ok=True)
        saved=archive/(hashlib.sha256(previous).hexdigest()+'.json')
        if saved.exists():
            if saved.read_bytes()!=previous:raise SystemExit('LOCK_ARCHIVE_CONFLICT')
        else:
            with saved.open('xb') as stream:stream.write(previous)
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=destination.parent,delete=False) as stream:
        json.dump(lock,stream,indent=2);stream.write('\n');stream.flush();os.fsync(stream.fileno());pending=Path(stream.name)
    if (destination.read_bytes() if destination.exists() else None)!=previous:raise SystemExit('LOCK_CHANGED')
    os.replace(pending,destination)
    print(json.dumps({'status':'prepared','image_id':image['Id'],'sources_digest':digest}))


if __name__=='__main__':main()
