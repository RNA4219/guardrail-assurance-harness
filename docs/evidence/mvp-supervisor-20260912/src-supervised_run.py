"""固定UC-CIの実行・再開・取消しを認証されたauthorityとjournalへ結ぶ。"""
from copy import deepcopy
import hashlib
import time

from gah.contracts import require_object, require_id, require_ref, require_uint
from gah.run_contracts import content_ref
from gah.wire import canonical_bytes

ENTRY_KEYS = ('obligation_id', 'case_id', 'trial_id', 'variant')
OPERATOR = 12004
VALIDATOR = 12003
ZERO_USAGE = {'input_tokens': 0, 'output_tokens': 0, 'cost_usd': '0'}


class SupervisorError(ValueError):
    pass


def validate_input(value):
    require_object(value, {'schema_version', 'run_id', 'contract_series_id', 'expected_contract_ref', 'trigger'})
    if type(value['schema_version']) is not int or value['schema_version'] != 1:
        raise SupervisorError('INVALID_REQUEST')
    for key in ('run_id', 'contract_series_id'):
        require_id(value[key])
    require_ref(value['expected_contract_ref'])
    if (len(value['run_id']) > 64 or value['expected_contract_ref']['kind'] != 'evaluation_contract'
            or value['trigger'] not in ('manual', 'change', 'scheduled_full')):
        raise SupervisorError('INVALID_REQUEST')
    return deepcopy(value)


class Supervisor:
    """runtime/runnerは信頼した固定実装。callerはrun単位のOS lockを保持する。"""
    def __init__(self, runtime, runner, checkpoint, request, *, clock=time.time, hook=None):
        self.request = validate_input(request)
        self.runtime, self.runner, self.checkpoint = runtime, runner, checkpoint
        self.clock = clock
        self.hook = hook or (lambda stage: None)
        self.run_id = request['run_id']
        self.tag = 'supervised-' + hashlib.sha256(canonical_bytes(self.request)).hexdigest()[:24]
        checkpoint.put('identity', {'request': self.request, 'runtime_prefix': runtime.prefix,
            'authority_image': runtime.lock['image_id'], 'fixture_image': runner.lock['image_id'],
            'fixture_digest': runner.lock['worker_digest'], 'adapter_digest': runner.adapter_digest,
            'isolation_digest': runner.isolation_digest})

    def now(self):
        value = self.clock()
        if type(value) not in (int, float) or not 0 <= value < float('inf'):
            raise SupervisorError('CLOCK_FAILURE')
        stamp = int(value)
        require_uint(stamp)
        return stamp

    def call(self, uid, action, key, *, durable=False, **fields):
        req = {'schema_version': 1, 'action': action, 'request_id': self.tag + '-' + key, **fields}
        if durable:
            self.checkpoint.put('request-' + key, {'uid': uid, 'request': req})
            self.hook('request-' + key)
        value = self.runtime.client(uid, req)
        if (type(value) is not dict or value.get('kind') == 'authority_error'
                or value.get('ci_eligible') is not False):
            reason = value.get('reason', 'AUTHORITY_UNAVAILABLE') if type(value) is dict else 'AUTHORITY_UNAVAILABLE'
            raise SupervisorError(reason)
        if ('action' in value and (value['action'] != action or value.get('request_id') != req['request_id'])):
            raise SupervisorError('AUTHORITY_RESPONSE_INVALID')
        if durable:
            self.checkpoint.put('response-' + key, value)
            self.hook('response-' + key)
        return value

    def prepare(self):
        value = self.call(OPERATOR, 'run_prepare', 'prepare', durable=True,
            run_id=self.run_id, contract_series_id=self.request['contract_series_id'],
            expected_contract_ref=self.request['expected_contract_ref'])
        bound = value['bound_run']
        manifest = bound['manifest']
        if (manifest['run_id'] != self.run_id or manifest['purpose'] != 'regression'
                or manifest['profile'] != 'full' or manifest['use_cases'] != ['UC-CI']
                or manifest['contract_ref'] != self.request['expected_contract_ref']
                or content_ref('trial_plan', bound['plan']['plan_id'], bound['plan']) != manifest['plan_ref']):
            raise SupervisorError('RUN_BINDING_MISMATCH')
        entries = bound['plan']['entries']
        material = value['materialization']
        records = material['manifest']['records']
        if (len(entries) != 30 or len(records) != len(entries)
                or material['manifest_ref'] != content_ref('fixture_manifest', material['manifest']['materialization_id'], material['manifest'])):
            raise SupervisorError('PLAN_MISMATCH')
        joined = []
        for index, record in enumerate(records):
            matches = [entry for entry in entries if all(entry[k] == record[k] for k in ENTRY_KEYS)]
            if len(matches) != 1:
                raise SupervisorError('PLAN_MISMATCH')
            entry = matches[0]
            if (entry['evaluator_ref']['digest'] != self.runner.lock['worker_digest']
                    or entry['target_ref']['digest'] != self.runner.target_digest(record['scenario'])
                    or len(entry['stage_ids']) != 1):
                raise SupervisorError('FIXTURE_BINDING_MISMATCH')
            joined.append((self.tag + '-op-' + str(index), entry, record))
        if len({canonical_bytes({k: entry[k] for k in ENTRY_KEYS}) for _,entry,_ in joined}) != len(entries):
            raise SupervisorError('PLAN_MISMATCH')
        self.bound, self.manifest, self.entries = bound, manifest, joined
        self.manifest_ref = content_ref('run_manifest', self.run_id, manifest)
        return manifest

    def status(self):
        value = self.call(OPERATOR, 'run_status', 'status', run_id=self.run_id)
        if (value['manifest'] != self.manifest or value['plan'] != self.bound['plan']
                or value['contract_series_id'] != self.request['contract_series_id']
                or value['resource_snapshot']['manifest_digest'] != self.manifest_ref['digest']):
            raise SupervisorError('RUN_BINDING_MISMATCH')
        return value['resource_snapshot']

    def operation(self, op):
        return self.call(OPERATOR, 'resource_operation', op + '-inspect', run_id=self.run_id,
            operation_id=op, expected_manifest_ref=self.manifest_ref)

    def claim(self):
        value = self.call(OPERATOR, 'resource_claim', 'claim', run_id=self.run_id,
            owner_id=self.tag + '-begin', recovery=False)
        self.owner = {'run_id': self.run_id, 'owner_id': value['owner_id'], 'owner_epoch': value['owner_epoch']}
        return self.owner

    def binding(self, op, entry, epoch):
        return {'run_id': self.run_id, 'operation_id': op, 'owner_epoch': epoch,
            'contract_digest': self.manifest['contract_ref']['digest'], 'target_digest': entry['target_ref']['digest'],
            **{key: entry[key] for key in ENTRY_KEYS if key != 'variant'}, 'stage_id': entry['stage_ids'][0],
            'fixture_digest': self.runner.lock['worker_digest'], 'adapter_digest': self.runner.adapter_digest,
            'policy_digest': self.manifest['policy_ref']['digest'], 'evaluator_digest': entry['evaluator_ref']['digest'],
            'isolation_digest': self.runner.isolation_digest}

    def gate(self):
        from tools.gah_ci import response_exit_code
        request = {'schema_version': 1, 'action': 'ci_check', 'request_id': self.tag + '-ci',
            'run_id': self.run_id, 'expected_manifest_ref': self.manifest_ref,
            'expected_contract_ref': self.manifest['contract_ref'], 'expected_baseline_ref': self.manifest['baseline_ref'],
            'expected_target_refs': self.manifest['target_refs'], 'expected_use_cases': self.manifest['use_cases']}
        value = self.runtime.client(OPERATOR, request)
        code = response_exit_code(request, value)
        return {'schema_version': 1, 'kind': 'supervised_run_result', 'run_id': self.run_id,
            'trigger': self.request['trigger'], 'executed_scope': 'full',
            'scope_reason': 'UNKNOWN_IMPACT_FULL_FALLBACK' if self.request['trigger'] == 'change' else 'FULL_REQUESTED',
            'control_ids': self.manifest['control_ids'], 'gate': value,
            'ci_eligible': value['ci_eligible'], 'exit_code': code}

    def journal_record(self, op):
        from gah.execution_journal import ExecutionJournal, JournalError
        with ExecutionJournal(self.runner.journal_path) as journal:
            try:
                return journal.get(self.run_id, op)
            except JournalError as error:
                if str(error) == 'NOT_FOUND':
                    return None
                raise

    def checked_receipt(self, op, entry, record, value, epoch):
        journal = self.journal_record(op)
        expected = self.binding(op, entry, epoch)
        if (journal is None or journal.get('receipt') != value or value['binding'] != expected
                or value['scenario'] != record['scenario'] or value['image_id'] != self.runner.lock['image_id']
                or value['ci_eligible'] is not False):
            raise SupervisorError('EXECUTION_RECEIPT_MISMATCH')
        return value

    def observe(self, op, receipt):
        if receipt['stop_confirmed'] is not True or receipt['cleanup_confirmed'] is not True:
            raise SupervisorError('STOP_UNCONFIRMED')
        value = self.call(VALIDATOR, 'resource_observe', op + '-observe', durable=True,
            run_id=self.run_id, operation_id=op, event_id=op + '-stop', stopped=True, usage=ZERO_USAGE)
        if value.get('accepted') is not True or value.get('conflict') is not False:
            raise SupervisorError('OBSERVATION_CONFLICT')

    def record_attempt(self, op, entry, receipt, timing):
        attempt = {'schema_version': 1, 'kind': 'attempt_record', 'attempt_id': op + '-attempt',
            'variant': entry['variant'], 'retry_of': None, 'started_at': timing['started_at'],
            'finished_at': timing['finished_at'], 'stop_confirmed': receipt['stop_confirmed'],
            'execution_status': receipt['execution_status'],
            'state_restored': receipt['cleanup_confirmed'] and receipt['isolation_config_verified'],
            'expected_binding': receipt['binding'], 'result': receipt['normalized_result']}
        value = self.call(VALIDATOR, 'evidence_record', op + '-record', durable=True, run_id=self.run_id, attempt=attempt)
        if value.get('accepted') is not True:
            raise SupervisorError('ATTEMPT_NOT_ACCEPTED')

    def one(self, op, entry, record):
        reserve_key = 'request-' + op + '-reserve'
        if self.checkpoint.get(reserve_key) is None:
            self.call(OPERATOR, 'resource_reserve', op + '-reserve', durable=True,
                **self.owner, operation_id=op, entry={k: entry[k] for k in ENTRY_KEYS}, scenario=record['scenario'])
        else:
            try:
                self.operation(op)
            except SupervisorError as error:
                if str(error) != 'OPERATION_MISSING':
                    raise
                saved = self.checkpoint.get(reserve_key)
                req = saved['request']
                if req['owner_epoch'] != self.owner['owner_epoch']:
                    raise SupervisorError('OWNER_STALE')
                self.call(OPERATOR, 'resource_reserve', op + '-reserve', durable=True,
                    **self.owner, operation_id=op, entry={k: entry[k] for k in ENTRY_KEYS}, scenario=record['scenario'])
        status = self.operation(op)
        if status['entry'] != entry or status['scenario'] != record['scenario'] or status['conflicted'] or status['released']:
            raise SupervisorError('OPERATION_CONFLICT')
        end = self.checkpoint.get('end-' + op)
        if end is not None:
            receipt = self.checked_receipt(op, entry, record, end['receipt'], status['owner_epoch'])
            self.observe(op, receipt)
            self.record_attempt(op, entry, receipt, end)
            return
        if status['owner_epoch'] != self.owner['owner_epoch']:
            raise SupervisorError('OWNER_STALE')
        if not status['dispatch_intended']:
            self.call(OPERATOR, 'resource_dispatch', op + '-dispatch', durable=True, **self.owner, operation_id=op)
        start = self.checkpoint.get('start-' + op)
        if start is not None or self.journal_record(op) is not None:
            # 開始後の中断から新規実行へ戻らない。実際の終了時刻が欠ければ正常結果にしない。
            recovered = self.runner.recover(self.run_id, op)
            self.checked_receipt(op, entry, record, recovered, status['owner_epoch'])
            self.checkpoint.put('recovered-' + op, {'receipt': recovered})
            self.observe(op, recovered)
            raise SupervisorError('INTERRUPTED_OPERATION')
        started = self.now()
        self.checkpoint.put('start-' + op, {'started_at': started, 'binding': self.binding(op, entry, status['owner_epoch'])})
        self.hook('start-' + op)
        receipt = self.runner.run(record['scenario'], self.binding(op, entry, status['owner_epoch']),
            run_deadline=self.manifest['deadline'], timeout_seconds=120)
        self.hook('runner-returned-' + op)
        finished = self.now()
        if finished < started:
            raise SupervisorError('CLOCK_FAILURE')
        self.checked_receipt(op, entry, record, receipt, status['owner_epoch'])
        end = {'started_at': started, 'finished_at': finished, 'receipt': receipt}
        self.checkpoint.put('end-' + op, end)
        self.hook('end-' + op)
        self.observe(op, receipt)
        self.record_attempt(op, entry, receipt, end)
        if receipt['execution_status'] != 'COMPLETED':
            raise SupervisorError('EXECUTION_INCOMPLETE')

    def unresolved_resources(self, snapshot):
        if snapshot['resources']['slots'] == 0 and snapshot['resources']['unsettled'] == 0:
            return []
        unresolved = []
        for op, _, _ in self.entries:
            try:
                state = self.operation(op)
            except SupervisorError as error:
                if str(error) == 'OPERATION_MISSING':
                    continue
                raise
            if state['dispatch_intended'] and (not state['stopped'] or not state['settled']):
                unresolved.append({'operation_id': op, 'stopped': state['stopped'], 'settled': state['settled'],
                    'reason': 'STOP_UNCONFIRMED' if not state['stopped'] else 'USAGE_UNCONFIRMED'})
        return unresolved

    def cancel(self, reason='CANCEL_REQUESTED'):
        if self.checkpoint.get('cancel-requested') is None:
            self.checkpoint.put('cancel-requested', {'reason': reason})
        before = self.gate()
        if before['gate']['outputs_ref'] is not None:
            return before
        acquired = self.call(OPERATOR, 'resource_cancel_claim', 'cancel-claim',
            run_id=self.run_id, owner_id=self.tag + '-begin')
        if acquired.get('already_terminal') is True:
            return self.gate()
        pending = []
        for op, entry, record in self.entries:
            try:
                status = self.operation(op)
            except SupervisorError as error:
                if str(error) == 'OPERATION_MISSING':
                    continue
                raise
            if not status['dispatch_intended']:
                continue
            try:
                receipt = self.runner.recover(self.run_id, op)
                self.checked_receipt(op, entry, record, receipt, status['owner_epoch'])
                self.observe(op, receipt)
                end = self.checkpoint.get('end-' + op)
                if end is not None and end['receipt'] == receipt:
                    self.record_attempt(op, entry, receipt, end)
            except Exception:
                pending.append({'operation_id': op, 'reason': 'STOP_UNCONFIRMED' if not status['stopped']
                    else 'RECOVERY_EVIDENCE_UNAVAILABLE'})
        if not pending:
            snapshot = self.status()
            if not snapshot['closed']:
                self.call(OPERATOR, 'resource_close', 'cancel-close', run_id=self.run_id,
                    owner_id=acquired['owner_id'], owner_epoch=acquired['owner_epoch'])
            self.call(OPERATOR, 'run_cancel_finalize', 'cancel-finalize', durable=True, run_id=self.run_id)
        result = self.gate()
        result['interruption_reason'] = self.checkpoint.get('cancel-requested')['reason']
        result['unresolved_operations'] = pending
        return result

    def execute(self, mode):
        if mode not in ('run', 'resume', 'cancel', 'status'):
            raise SupervisorError('INVALID_MODE')
        if mode != 'run' and self.checkpoint.get('request-prepare') is None:
            raise SupervisorError('CHECKPOINT_REQUIRED')
        self.prepare()
        if mode in ('run', 'resume'):
            self.call(OPERATOR, 'run_begin', 'begin', durable=True, manifest=self.manifest,
                plan=self.bound['plan'], contract_series_id=self.request['contract_series_id'])
        snapshot = self.status()
        if mode == 'status':
            result = self.gate()
            result['resources'] = snapshot
            result['unresolved_operations'] = self.unresolved_resources(snapshot)
            return result
        if mode == 'cancel' or snapshot['cancelled'] or self.checkpoint.get('cancel-requested') is not None:
            return self.cancel()
        previous = self.gate()
        if previous['gate']['outputs_ref'] is not None:
            return previous
        try:
            self.call(OPERATOR, 'evidence_open', 'open', durable=True, run_id=self.run_id)
            if not snapshot['closed']:
                for op, entry, record in self.entries:
                    self.claim()
                    self.one(op, entry, record)
                self.claim()
                self.call(OPERATOR, 'resource_close', 'close', durable=True, **self.owner)
            self.call(OPERATOR, 'evidence_finalize', 'finalize', durable=True, run_id=self.run_id)
            return self.gate()
        except (SupervisorError, KeyboardInterrupt) as error:
            return self.cancel('INTERRUPTED' if isinstance(error, KeyboardInterrupt) else str(error))
