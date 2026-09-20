"""明示v6診断CLIの開始・再送と入力境界。Dockerを起動しない。"""
from copy import deepcopy
from unittest.mock import patch
import unittest

from tests import test_partitioned_diagnostic_run as diagnostic
from tools import gah_run
from gah.partitioned_diagnostic_run import _operation_id


class PartitionedDiagnosticCliTests(unittest.TestCase):
    setUp = diagnostic.PartitionedDiagnosticRunTests.setUp

    def launch_request(self):
        self.runtime.database_mode = 'partitioned-v6'
        return {'schema_version': 1, 'kind': 'partitioned_diagnostic_request',
                'begin_request': {'schema_version': 1, 'action': 'run_begin_v2',
                    'request_id': 'diagnostic-begin', 'manifest': deepcopy(self.manifest),
                    'contract_series_id': 'partition-series', 'expected_generation': 1,
                    'execution_profile': deepcopy(self.profile)},
                'selector': deepcopy(self.selector)}

    def result(self):
        operation_id = _operation_id(self.manifest, self.selector)
        return {'schema_version': 1, 'kind': 'partitioned_diagnostic_entry_result',
                'operation_id': operation_id, 'attempt_id': operation_id + '-attempt',
                'execution_status': 'COMPLETED',
                'run_id': self.run_id, 'resource_closed': True,
                'diagnostic_finalized': False, 'ci_eligible': False,
                'authority_connected': False, 'resource_closure_verified': False,
                'baseline_freshness_verified': False, 'adoption_verified': False,
                'admission_verified': False}

    def test_launch_and_replay_keep_one_saved_begin_and_no_legacy_action(self):
        request = self.launch_request()
        with patch('gah.partitioned_diagnostic_run.execute_one_entry', return_value=self.result()) as execute:
            first = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)
            second = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)
        self.assertEqual(first['exit_code'], 0)
        self.assertEqual(second, first)
        self.assertEqual(execute.call_count, 2)
        self.assertEqual([value['action'] for _, value in self.runtime.calls], ['run_begin_v2'])
        self.assertFalse(first['ci_eligible'])

    def test_unknown_keys_bool_generation_and_foreign_use_case_are_rejected_before_dispatch(self):
        source = self.launch_request()
        invalid = []
        value = deepcopy(source); value['worker'] = 'arbitrary'; invalid.append(value)
        value = deepcopy(source); value['begin_request']['expected_generation'] = True; invalid.append(value)
        value = deepcopy(source); value['begin_request']['manifest']['use_cases'] = ['UC-LLM']; invalid.append(value)
        value = deepcopy(source); value['selector']['variant'] = 'baseline'; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), patch('gah.partitioned_diagnostic_run.execute_one_entry') as execute:
                response = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, value)
                self.assertEqual(response['exit_code'], 2)
                self.assertFalse(response['ci_eligible'])
                execute.assert_not_called()
        self.assertEqual(self.runtime.calls, [])

    def test_input_validation_returns_an_independent_request(self):
        source = self.launch_request()
        value = gah_run.validate_diagnostic_input(source)
        value['selector']['case_id'] = 'changed'
        value['begin_request']['manifest']['run_id'] = 'changed'
        self.assertEqual(source['selector'], self.selector)
        self.assertEqual(source['begin_request']['manifest'], self.manifest)

    def test_changed_launch_request_cannot_reuse_saved_begin(self):
        request = self.launch_request()
        with patch('gah.partitioned_diagnostic_run.execute_one_entry', return_value=self.result()) as execute:
            self.assertEqual(gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)['exit_code'], 0)
            changed = deepcopy(request); changed['selector']['case_id'] = 'other-case'
            response = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, changed)
            self.assertEqual(response['reason'], 'CHECKPOINT_CONFLICT')
            self.assertEqual(execute.call_count, 1)
        self.assertEqual(len(self.runtime.calls), 1)

    def test_mismatched_begin_response_is_not_saved_or_executed(self):
        request = self.launch_request()
        original = self.runtime.client
        def wrong(uid, value):
            response = original(uid, value)
            response['request_id'] = 'other-request'
            return response
        with patch.object(self.runtime, 'client', side_effect=wrong), patch(
                'gah.partitioned_diagnostic_run.execute_one_entry') as execute:
            result = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)
        self.assertEqual(result['reason'], 'BEGIN_RESPONSE_MISMATCH')
        execute.assert_not_called()
        self.assertIsNone(gah_run.Checkpoint(self.folder/'diagnostic-launch').get('begin-response'))

    def test_default_database_mode_is_rejected_before_dispatch(self):
        request = self.launch_request(); self.runtime.database_mode = 'default'
        with patch('gah.partitioned_diagnostic_run.execute_one_entry') as execute:
            response = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)
        self.assertEqual(response['reason'], 'FIXED_V6_RUNTIME_REQUIRED')
        self.assertEqual(self.runtime.calls, [])
        execute.assert_not_called()

    def test_ineligible_or_unclosed_consumer_result_never_returns_zero(self):
        request = self.launch_request()
        for field in ('ci_eligible', 'resource_closed', 'baseline_freshness_verified'):
            value = self.result(); value[field] = not value[field]
            with self.subTest(field=field), patch('gah.partitioned_diagnostic_run.execute_one_entry', return_value=value):
                response = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)
                self.assertEqual(response['reason'], 'DIAGNOSTIC_RESULT_INVALID')
                self.assertEqual(response['exit_code'], 2)
                self.assertFalse(response['ci_eligible'])

    def test_malformed_or_foreign_consumer_result_never_returns_zero(self):
        request = self.launch_request()
        invalid = []
        value = self.result(); del value['operation_id']; invalid.append(value)
        value = self.result(); value['operation_id'] = 'other-operation'; invalid.append(value)
        value = self.result(); value['attempt_id'] = 'other-attempt'; invalid.append(value)
        value = self.result(); value['execution_status'] = 'TIMEOUT'; invalid.append(value)
        value = self.result(); value['schema_version'] = True; invalid.append(value)
        value = self.result(); value['unexpected'] = False; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value), patch('gah.partitioned_diagnostic_run.execute_one_entry', return_value=value):
                response = gah_run.execute_diagnostic(self.runtime, self.runner, self.folder, request)
                self.assertEqual(response['reason'], 'DIAGNOSTIC_RESULT_INVALID')
                self.assertEqual(response['exit_code'], 2)
                self.assertFalse(response['ci_eligible'])


if __name__ == '__main__':
    unittest.main()
