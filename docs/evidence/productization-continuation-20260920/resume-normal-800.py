import contextlib, hashlib, json, shutil, sqlite3, sys, time, unittest
from pathlib import Path
ROOT=Path.cwd()
sys.path[:0]=[str(ROOT/'src'), str(ROOT)]
stage=ROOT/'.ga/productization-implementation-20260919/partitioned-normal-resume-20260920'
stage.mkdir(exist_ok=True)
source=ROOT/'.ga/productization-implementation-20260919/partitioned-transition-journey-v5-linux/source-before.json'
checkpoint=ROOT/'.ga/productization-implementation-20260919/partitioned-transition-journey-v5-linux/boundary-6.sqlite'
def hashes():
    paths=sorted(set(p for folder in ('src','tools','tests','fixtures') for p in (ROOT/folder).rglob('*.py'))|set((ROOT/'config').glob('*runtime.lock.json')))
    return {p.relative_to(ROOT).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
before=hashes()
original_manifest=json.loads(source.read_text(encoding='utf-8'))
source_matches_initial=before==original_manifest
input_digest=hashlib.sha256(checkpoint.read_bytes()).hexdigest()
started=time.perf_counter()
log=(stage/'tests.log').open('x',encoding='utf-8')
class Tee:
    def write(self,value):
        log.write(value); log.flush(); sys.__stdout__.write(value); sys.__stdout__.flush()
    def flush(self): log.flush(); sys.__stdout__.flush()
tee=Tee()
counts={}
start_counts={}
last_progress=started
milestones=[]
from gah.adoption import AdoptionStore
original_dispatch=AdoptionStore.dispatch
def dispatch(self,*args,**kwargs):
    global last_progress
    request=args[2] if len(args)>2 else kwargs.get('request',{})
    action=request.get('action')
    run_id=request.get('run_id')
    profile=None
    if action=='resource_start':
        start_counts[run_id]=start_counts.get(run_id,0)+1
        if start_counts[run_id]==2:
            import cProfile
            profile=cProfile.Profile(); profile.enable()
    try:
        response=original_dispatch(self,*args,**kwargs)
    finally:
        if profile is not None:
            profile.disable()
            profile.dump_stats(str(stage/(str(run_id)+'-warm-start.pstats')))
    if action=='evidence_complete':
        run_id=request['run_id']; counts[run_id]=counts.get(run_id,0)+1
        now=time.perf_counter()
        if counts[run_id]%100==0 or now-last_progress>45:
            print('PROGRESS',run_id,counts[run_id],round(now-started,2),flush=True)
            last_progress=now
    if action in ('ci_check','evidence_finalize','baseline_adopt'):
        item={'action':action,'run_id':request.get('run_id'),'elapsed_seconds':round(time.perf_counter()-started,3),'counts':dict(counts)}
        milestones.append(item)
        print('BOUNDARY',json.dumps(item),flush=True)
        (stage/'milestones.json').write_text(json.dumps(milestones,indent=2),encoding='utf-8')
        if action in ('ci_check','evidence_finalize','baseline_adopt'):
            path=stage/('boundary-'+str(len(milestones))+'-'+action+'.sqlite')
            assert not self._db.in_transaction
            with sqlite3.connect(path) as destination: self._db.backup(destination)
    return response
AdoptionStore.dispatch=dispatch
def run_case():
    from gah.run_contracts import content_ref
    from tests import test_fixture_admission as fixture
    from tests.test_partitioned_transition_integration import PartitionedTransitionIntegrationTests,request
    if not source_matches_initial:
        raise AssertionError('product/test source changed since the interrupted run')
    fixture_case=fixture.FixtureAdmissionTests('test_prepare_contract_adoption_and_run_begin_use_real_bound_objects')
    fixture_case.setUp()
    store=None
    try:
        shutil.copyfile(checkpoint,fixture_case.path)
        store=fixture_case.open()
        case=PartitionedTransitionIntegrationTests('test_all_400_cases_close_finalize_and_adopt_baseline')
        case.fixture=fixture_case
        case.store=store
        for run_id,expected in (('partitioned-llm-full-400',400),('partitioned-llm-v2-old',400),('partitioned-llm-v2-new',800)):
            actual=store._db.execute('SELECT COUNT(*) FROM attempts WHERE run_id=?',(run_id,)).fetchone()[0]
            case.assertEqual(actual,expected,'adopted comparison checkpoint must contain the completed evidence')
        case.assertEqual(store._db.execute('SELECT COUNT(*) FROM attempts WHERE run_id=?',('partitioned-llm-normal-v2',)).fetchone()[0],0,'normal 800 run must start once from the adopted-candidate checkpoint')
        contract_series='contract-series-partitioned-llm-full-400'
        baseline_series='partitioned-llm-baseline-series'
        current=store.dispatch(12004,12004,request('contract_current','normal-resume-v2-current-contract',series_id=contract_series))
        case.assertTrue(current['valid'],current)
        case.assertEqual(current['generation'],2)
        case.assertFalse(current['ci_eligible'])
        baseline=store.dispatch(12004,12004,request('baseline_current','normal-resume-v2-current-baseline',series_id=baseline_series))
        case.assertTrue(baseline['valid'],baseline)
        case.assertEqual(baseline['generation'],1)
        following=current['contract']
        following_ref=content_ref('evaluation_contract',following['contract_id'],following)
        case._run_normal_and_refresh(contract_series,following_ref,baseline_series)
        final_count=store._db.execute('SELECT COUNT(*) FROM attempts WHERE run_id=?',('partitioned-llm-normal-v2',)).fetchone()[0]
        case.assertEqual(final_count,800)
        case.assertEqual(counts.get('partitioned-llm-normal-v2',0),800)
    finally:
        if store is not None: store.close()
        fixture_case.doCleanups()
suite=unittest.TestSuite([unittest.FunctionTestCase(run_case,description='resume the 800-case normal CLI acceptance from the adopted candidate checkpoint')])
with contextlib.redirect_stdout(tee),contextlib.redirect_stderr(tee):
    result=unittest.TextTestRunner(stream=tee,verbosity=2).run(suite)
after=hashes()
summary={'test':'normal_cli_800_resume_outputs_ci_and_baseline_refresh','tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'passed':result.wasSuccessful() and source_matches_initial and before==after,'elapsed_seconds':round(time.perf_counter()-started,3),'source_matches_interrupted_run':source_matches_initial,'source_unchanged':before==after,'changed_files':sorted(k for k in before.keys()|after.keys() if before.get(k)!=after.get(k)),'checkpoint_sha256':input_digest,'checkpoint':'partitioned-transition-journey-v5-linux/boundary-6.sqlite','worker_kind':'inprocess_fixed_worker','docker_verified':False,'completed_entries':counts}
(stage/'result.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
print(json.dumps(summary),flush=True)
log.close()
sys.exit(0 if summary['passed'] and result.wasSuccessful() else 1)