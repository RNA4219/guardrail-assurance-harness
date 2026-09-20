"""固定query-scale入力とworker出力を結ぶ。採択・現在性はauthorityの責務。"""
from copy import deepcopy
from functools import lru_cache
import hashlib
from pathlib import Path

from .contracts import ContractError, decode_document, require_object
from . import execution_profiles, guardrail_results, guardrail_runtime, llm_materialization
from .docker_runner import PROFILE
from .normalized import validate_binding
from .partitioned_run_contracts import bind_partitioned_run_manifest
from .partitioned_scale_corpus import partition_scale_corpus, restore_scale_corpus
from .partitioned_trial_plan import restore_trial_plan
from .query_scale_data import build_scale_corpus
from .run_contracts import content_ref
from .wire import canonical_bytes

ROOT = Path(__file__).resolve().parents[2]
_CORPUS_SOURCES = (
    'query_scale_data.py', 'evaluation_data.py', 'corpus.py', 'contracts.py',
    'wire.py', 'run_contracts.py', 'partitioned_case_set.py',
    'partitioned_scale_corpus.py', 'partitioned_trial_plan.py',
)
_IDENTITY = ('obligation_id', 'case_id', 'trial_id', 'variant')


def _count(value):
    if type(value) is not int or value not in (400, 800, 1600):
        raise ContractError('QUERY_SCALE_COUNT_INVALID')
    return value


def _source_key():
    return tuple(hashlib.sha256((ROOT/'src/gah'/name).read_bytes()).hexdigest()
                 for name in _CORPUS_SOURCES)


@lru_cache(maxsize=3)
def _fixed_corpus(case_count, source_key):
    corpus = build_scale_corpus(case_count)
    index, _, _, _ = partition_scale_corpus(corpus)
    return {
        'index': index,
        'cases': {item['case_id']: item for item in corpus['case_set']['cases']},
        'documents': {canonical_bytes(item['ref']): item['document'] for item in corpus['documents']},
    }


def _context(case_count):
    from .partitioned_llm_materialization import evaluator_document
    fixed = _fixed_corpus(_count(case_count), _source_key())
    evaluator = evaluator_document(fixed['index'])
    return (fixed, content_ref('evaluator', evaluator['evaluator_id'], evaluator),
            guardrail_runtime.read_lock())


def validate_request(value, *, case_count):
    """固定family全体は一度index化し、要求1件だけを独立treeへコピーする。"""
    require_object(value, {'schema_version', 'kind', 'target', 'stages'})
    if type(value['schema_version']) is not int or value['schema_version'] != 1 or value['kind'] != 'guardrail_case_request':
        raise ContractError('CASE_REQUEST_INVALID')
    fixed, evaluator_ref, lock = _context(case_count)
    document = value['target']
    if (type(document) is not dict or type(document.get('behavior_version')) is not str
            or document['behavior_version'] not in llm_materialization.VERSIONS
            or document != llm_materialization.target_document(document['behavior_version'], lock['image_id'])):
        raise ContractError('TARGET_INVALID')
    target_ref = content_ref('target', document['target_id'], document)
    stages = value['stages']
    if type(stages) is not list or len(stages) != 1:
        raise ContractError('STAGES_INVALID')
    require_object(stages[0], {'binding', 'input'})
    binding = validate_binding(stages[0]['binding'])
    if (binding['target_digest'] != target_ref['digest']
            or binding['evaluator_digest'] != evaluator_ref['digest']
            or binding['fixture_digest'] != lock['worker_digest']
            or binding['adapter_digest'] != lock['source_sha256']['src/gah/normalized.py']
            or binding['isolation_digest'] != hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()):
        raise ContractError('BINDING_MISMATCH')
    case = fixed['cases'].get(binding['case_id'])
    if case is None or binding['stage_id'] != case['session_steps'][0]['stage_id']:
        raise ContractError('CASE_STAGE_MISMATCH')
    expected_input = fixed['documents'][canonical_bytes(case['session_steps'][0]['input_ref'])]
    if stages[0]['input'] != expected_input:
        raise ContractError('INPUT_MATERIALIZATION_MISMATCH')
    if len(canonical_bytes(value)) > 65536:
        raise ContractError('CASE_SIZE')
    return deepcopy(value)


def validate_bundle(bundle, *, case_count):
    require_object(bundle, {'request', 'worker_result'})
    request = validate_request(bundle['request'], case_count=case_count)
    return guardrail_results.validate_worker_result(request, bundle['worker_result'])


def from_worker(raw, request, *, case_count):
    if type(raw) is not bytes or len(raw) > 65536:
        raise ContractError('OUTPUT_TOO_LARGE')
    bundle = {'request': deepcopy(request), 'worker_result': decode_document(raw)}
    validate_bundle(bundle, case_count=case_count)
    return bundle


class PreparedCases:
    """分割artifactを一度照合して保持する、未採択のprivate実行入力。"""

    def __init__(self, prepared):
        try:
            count = _count(prepared['corpus_index']['case_count'])
            fixed, evaluator_ref, lock = _context(count)
            corpus = restore_scale_corpus(prepared['corpus_index'], prepared['case_set_index'],
                                          prepared['case_set_segments'], prepared['document_segments'])
            if (prepared['corpus_index'] != fixed['index'] or prepared['case_set'] != corpus['case_set']
                    or content_ref('evaluator', prepared['evaluator_document']['evaluator_id'],
                                   prepared['evaluator_document']) != evaluator_ref):
                raise ContractError('INPUT_MATERIALIZATION_MISMATCH')
            bound = bind_partitioned_run_manifest(
                prepared['manifest'], prepared['contract'], prepared['plan_index'], prepared['plan_segments'],
                prepared['policy'], prepared['registry'], prepared['case_set'],
                baseline_context=prepared['baseline_context'])
            saved_bound = prepared['bound_run']
            if type(saved_bound) is dict and '_partitioned_context' in saved_bound:
                from .partitioned_run_contracts import validate_partitioned_runtime
                saved_bound = validate_partitioned_runtime(
                    saved_bound, baseline_context=prepared['baseline_context'])['_partitioned_receipt']
            if bound != saved_bound:
                raise ContractError('BINDING_MISMATCH')
            plan = restore_trial_plan(prepared['plan_index'], prepared['plan_segments'])
            execution_profiles.check_plan(prepared['execution_profile'], {'manifest': prepared['manifest'], 'plan': plan})
            targets = {}
            for version in sorted(llm_materialization.VERSIONS):
                document = llm_materialization.target_document(version, lock['image_id'])
                targets[content_ref('target', document['target_id'], document)['digest']] = document
            for entry in plan['entries']:
                if entry['target_ref']['digest'] not in targets or entry['evaluator_ref'] != evaluator_ref:
                    raise ContractError('BINDING_MISMATCH')
                profile = execution_profiles.expected(prepared['execution_profile'],
                                                      entry['target_ref']['digest'], evaluator_ref['digest'])
                if (profile['fixture_digest'] != lock['worker_digest']
                        or profile['adapter_digests'] != [lock['source_sha256']['src/gah/normalized.py']]
                        or profile['isolation_digest'] != hashlib.sha256(canonical_bytes(PROFILE)).hexdigest()):
                    raise ContractError('BINDING_MISMATCH')
            self._case_count = count
            self._manifest = deepcopy(prepared['manifest'])
            self._profile = execution_profiles.validate(prepared['execution_profile'])
            self._entries = {tuple(entry[key] for key in _IDENTITY): entry for entry in plan['entries']}
            self._cases = {item['case_id']: item for item in corpus['case_set']['cases']}
            self._documents = {canonical_bytes(item['ref']): item['document'] for item in corpus['documents']}
            self._targets = targets
        except ContractError:
            raise
        except (KeyError, TypeError, ValueError, IndexError):
            raise ContractError('MATERIALIZATION_INVALID') from None

    def for_entry(self, entry, operation_id, owner_epoch):
        """登録済みentryの完全一致を確認し、所有者情報を含む要求を作る。"""
        try:
            expected_entry = self._entries.get(tuple(entry[key] for key in _IDENTITY))
        except (KeyError, TypeError):
            expected_entry = None
        if expected_entry is None or entry != expected_entry:
            raise ContractError('ENTRY_NOT_PLANNED')
        profile = execution_profiles.expected(self._profile, entry['target_ref']['digest'], entry['evaluator_ref']['digest'])
        stage = self._cases[entry['case_id']]['session_steps'][0]
        binding = {
            'run_id': self._manifest['run_id'], 'operation_id': operation_id, 'owner_epoch': owner_epoch,
            'contract_digest': self._manifest['contract_ref']['digest'], 'target_digest': entry['target_ref']['digest'],
            'obligation_id': entry['obligation_id'], 'case_id': entry['case_id'], 'trial_id': entry['trial_id'],
            'stage_id': stage['stage_id'], 'fixture_digest': profile['fixture_digest'],
            'adapter_digest': profile['adapter_digests'][0], 'policy_digest': self._manifest['policy_ref']['digest'],
            'evaluator_digest': entry['evaluator_ref']['digest'], 'isolation_digest': profile['isolation_digest'],
        }
        request = {'schema_version': 1, 'kind': 'guardrail_case_request',
                   'target': self._targets[entry['target_ref']['digest']],
                   'stages': [{'binding': binding, 'input': self._documents[canonical_bytes(stage['input_ref'])]}]}
        return validate_request(request, case_count=self._case_count)
