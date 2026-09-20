"""固定query-scale family専用。既存の隔離・journal・回収処理を再利用する。"""
import hashlib
from .contracts import require_uint, ContractError
from .guardrail_runner import GuardrailRunner
from . import partitioned_guardrail_results
from .wire import canonical_bytes


class PartitionedGuardrailRunner(GuardrailRunner):
    def __init__(self, journal_path, *, case_count, **kwargs):
        if type(case_count) is not int or case_count not in (400, 800, 1600):
            raise ContractError('QUERY_SCALE_COUNT_INVALID')
        self.case_count = case_count
        super().__init__(journal_path, **kwargs)

    def run(self, request, *, run_deadline, timeout_seconds=120, cancel_event=None):
        return self.run_partitioned(request, case_count=self.case_count, run_deadline=run_deadline,
                                    timeout_seconds=timeout_seconds, cancel_event=cancel_event)
