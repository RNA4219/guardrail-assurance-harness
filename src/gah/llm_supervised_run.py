"""固定UC-LLMの通常比較を既存監督の予約・再開・取消しへ接続する。"""
import hashlib
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from . import execution_profiles, guardrail_results
from .contracts import ContractError, require_uint
from .run_contracts import content_ref
from .supervised_run import Supervisor, SupervisorError, OPERATOR, VALIDATOR, ENTRY_KEYS
from .wire import canonical_bytes


class LlmSupervisor(Supervisor):
    def prepare(self):
        if 'changed_refs' in self.request:
            raise SupervisorError('LLM_SCOPE_NOT_CONNECTED')
        prepared = self.call(OPERATOR, 'run_prepare', 'prepare', durable=True,
            run_id=self.run_id, contract_series_id=self.request['contract_series_id'],
            expected_contract_ref=self.request['expected_contract_ref'])
        bound = prepared['bound_run']
        manifest = bound['manifest']
        material = prepared['materialization']
        entries = bound['plan']['entries']
        if (manifest['run_id'] != self.run_id or manifest['purpose'] != 'regression'
                or manifest['profile'] != 'full' or manifest['use_cases'] != ['UC-LLM']
                or manifest['contract_ref'] != self.request['expected_contract_ref']
                or content_ref('trial_plan', bound['plan']['plan_id'], bound['plan']) != manifest['plan_ref']
                or material['kind'] != 'llm_input_materialization'
                or material['run_id'] != self.run_id
                or material['manifest_ref'] != content_ref('run_manifest', self.run_id, manifest)
                or material['profile_ref'] != content_ref('execution_profile', self.run_id, prepared['execution_profile'])
                or material['planned_trials'] != len(entries)
                or material['planned_stages'] != sum(len(e['stage_ids']) for e in entries)
                or material['case_count'] != 400 or len(entries) != 800):
            raise SupervisorError('RUN_BINDING_MISMATCH')
        try:
            execution_profiles.check_plan(prepared['execution_profile'], bound)
            joined = []
            for index, entry in enumerate(entries):
                profile = execution_profiles.expected(prepared['execution_profile'],
                    entry['target_ref']['digest'], entry['evaluator_ref']['digest'])
                if (profile['fixture_digest'] != self.runner.lock['worker_digest']
                        or profile['adapter_digests'] != [self.runner.adapter_digest]
                        or profile['isolation_digest'] != self.runner.isolation_digest):
                    raise SupervisorError('EXECUTION_PROFILE_MISMATCH')
                target = guardrail_results.target_for_entry(prepared, entry)
                if target['runtime_image_id'] != self.runner.lock['image_id']:
                    raise SupervisorError('EXECUTION_PROFILE_MISMATCH')
                joined.append((self.tag + '-op-' + str(index), entry,
                    {'scenario': 'guardrail:' + target['behavior_version']}))
            if len({canonical_bytes({k: e[k] for k in ENTRY_KEYS}) for _,e,_ in joined}) != len(entries):
                raise SupervisorError('PLAN_MISMATCH')
        except ContractError as error:
            raise SupervisorError('RUN_BINDING_MISMATCH') from error
        self.prepared = prepared
        self.bound, self.manifest, self.entries = bound, manifest, joined
        self.scope = None
        self.manifest_ref = content_ref('run_manifest', self.run_id, manifest)
        return manifest

    def has_previous_operation(self, op):
        key = op + '-operation-start'
        # 既存の予約・中断記録がある操作は、現在前提と保存操作を読む復旧経路へ戻す。
        saved = any(self.checkpoint.get(name) is not None for name in (
            'request-' + key, 'request-' + op + '-reserve', 'start-' + op, 'end-' + op))
        return saved or self.journal_record(op) is not None

    def begin_operation(self, op, entry, record):
        if self.has_previous_operation(op):
            return super().begin_operation(op, entry, record)
        status = self.begin_new_operation(op, entry, record)
        return self.complete_operation(op, entry, record, status)

    def begin_new_operation(self, op, entry, record):
        key = op + '-operation-start'
        value = self.call(OPERATOR, 'resource_start', key, durable=True,
            run_id=self.run_id, owner_id=self.tag + '-begin', operation_id=op,
            entry={k:entry[k] for k in ENTRY_KEYS}, scenario=record['scenario'],
            expected_manifest_ref=self.manifest_ref)
        if (value.get('manifest_ref') != self.manifest_ref or value.get('owner_id') != self.tag + '-begin'
                or type(value.get('owner_epoch')) is not int or value['owner_epoch'] < 1
                or value.get('operation',{}).get('operation_id') != op
                or value['operation'].get('owner_epoch') != value['owner_epoch']
                or value['operation'].get('dispatch_intended') is not True):
            raise SupervisorError('OPERATION_CONFLICT')
        self.owner = {'run_id':self.run_id, 'owner_id':value['owner_id'], 'owner_epoch':value['owner_epoch']}
        return value['operation']

    def run_entries(self):
        limit = self.bound['policy']['profiles'][self.manifest['profile']]['concurrent_evaluations']
        if type(limit) is not int or not 1 <= limit <= 4:
            raise SupervisorError('CONCURRENCY_LIMIT')
        entries = iter(self.entries)
        pending = {}
        exhausted = False
        # authority、owner、checkpointは呼出スレッドだけが操作する。
        # workerだけを並列化し、停止・保存の確認後に次の枠を使う。
        with ThreadPoolExecutor(max_workers=limit) as pool:
            while pending or not exhausted:
                while not exhausted and len(pending) < limit:
                    item = next(entries, None)
                    if item is None:
                        exhausted = True
                        break
                    op, entry, record = item
                    if self.has_previous_operation(op):
                        self.begin_operation(op, entry, record)
                        continue
                    status = self.begin_new_operation(op, entry, record)
                    started = self.prepare_operation(op, entry, record, status)
                    if started is not None:
                        epoch = status['owner_epoch']
                        future = pool.submit(self.run_worker, op, entry, record, epoch)
                        pending[future] = (op, entry, record, epoch, started)
                if not pending:
                    continue
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    args = pending.pop(future)
                    try:
                        receipt, finished = future.result()
                    except Exception as error:
                        raise SupervisorError('EXECUTION_UNAVAILABLE') from error
                    self.finish_operation(*args, receipt, finished=finished)

    def run_worker(self, op, entry, record, epoch):
        receipt = self.execute_runner(op, entry, record, epoch)
        return receipt, self.now()

    def binding(self, op, entry, epoch):
        return guardrail_results.for_entry(self.prepared, entry, op, epoch)['stages'][0]['binding']

    def execute_runner(self, op, entry, record, epoch):
        request = guardrail_results.for_entry(self.prepared, entry, op, epoch)
        return self.runner.run(request, run_deadline=self.manifest['deadline'], timeout_seconds=120)

    def checked_receipt(self, op, entry, record, value, epoch):
        request = guardrail_results.for_entry(self.prepared, entry, op, epoch)
        journal = self.journal_record(op)
        if (journal is None or journal.get('receipt') != value
                or value['binding'] != request['stages'][0]['binding']
                or value['scenario'] != 'guardrail:' + hashlib.sha256(canonical_bytes(request)).hexdigest()
                or value['image_id'] != self.runner.lock['image_id']
                or value['kind'] != 'guardrail_execution' or value['ci_eligible'] is not False
                or value['case_result'] is not None and value['case_result']['request'] != request):
            raise SupervisorError('EXECUTION_RECEIPT_MISMATCH')
        return value

    def completion_timing(self, started, finished, receipt):
        return {**super().completion_timing(started, finished, receipt),
            'clock_domain':'supervisor_host_utc', 'worker_clock_domain':'authority_runtime_utc'}

    def case_attempts(self, op, entry, receipt, timing):
        # 未完了のcaseを正常な段階へ補完しない。停止精算後に取消しへ進める。
        if receipt['execution_status'] != 'COMPLETED':
            return []
        bundle = receipt['case_result']
        try:
            require_uint(timing['started_at']); require_uint(timing['finished_at'])
            if timing['finished_at'] < timing['started_at']:
                raise SupervisorError('CLOCK_FAILURE')
            results = guardrail_results.validate_bundle(bundle)
        except ContractError as error:
            raise SupervisorError('OUTPUT_REJECTED') from error
        attempts = []
        for index, (result, stage) in enumerate(zip(results, bundle['worker_result']['stage_timings'])):
            # hostのUTCとLinux runtimeのUTCは同じ精度・同期を仮定しない。
            # workerの実時刻は変更せず、authorityが配送意図・停止観測・現在時刻で照合する。
            attempt = {'schema_version': 1, 'kind': 'attempt_record',
                'attempt_id': op + '-attempt-' + str(index), 'variant': entry['variant'], 'retry_of': None,
                'started_at': stage['started_at'], 'finished_at': stage['finished_at'],
                'stop_confirmed': receipt['stop_confirmed'], 'execution_status': receipt['execution_status'],
                'state_restored': receipt['cleanup_confirmed'] and receipt['isolation_config_verified'],
                'expected_binding': result['binding'], 'result': result}
            attempts.append(attempt)
        return attempts

    def record_attempt(self, op, entry, receipt, timing):
        for index, attempt in enumerate(self.case_attempts(op, entry, receipt, timing)):
            saved = self.call(VALIDATOR, 'evidence_record', op + '-record-' + str(index), durable=True,
                run_id=self.run_id, attempt=attempt)
            if saved.get('accepted') is not True:
                raise SupervisorError('ATTEMPT_NOT_ACCEPTED')

    def record_completion(self, op, entry, receipt, timing):
        if receipt['execution_status'] != 'COMPLETED':
            return super().record_completion(op, entry, receipt, timing)
        if receipt['stop_confirmed'] is not True or receipt['cleanup_confirmed'] is not True:
            raise SupervisorError('STOP_UNCONFIRMED')
        attempts = self.case_attempts(op, entry, receipt, timing)
        saved = self.call(VALIDATOR, 'evidence_complete', op + '-complete', durable=True,
            run_id=self.run_id, operation_id=op, event_id=op + '-stop',
            usage=receipt['case_result']['worker_result']['usage'], attempts=attempts)
        if (saved.get('accepted') is not True or type(saved.get('records')) is not list
                or len(saved['records']) != len(attempts)
                or any(record.get('accepted') is not True for record in saved['records'])):
            raise SupervisorError('ATTEMPT_NOT_ACCEPTED')
