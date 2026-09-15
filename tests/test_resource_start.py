"""固定操作の開始は同一transactionで完了し、失敗・再配送で開始権を復活させない。"""
from copy import deepcopy
import unittest
from unittest.mock import patch
from tests import test_resource_authority as seed
from gah import resource_authority as authority
from gah.contracts import ContractError
from gah.resources import ResourceBook, ResourceError
from gah.run_contracts import content_ref


class ResourceStartTests(unittest.TestCase):
    setUp=seed.ResourceAuthorityTests.setUp
    tearDown=seed.ResourceAuthorityTests.tearDown
    _tx=seed.ResourceAuthorityTests._tx
    _execute=seed.ResourceAuthorityTests._execute
    _check_start=seed.ResourceAuthorityTests._check_start
    def request(self):
        return {'schema_version':1,'action':'resource_start','request_id':'start','run_id':'run-1',
            'owner_id':'operator-1','operation_id':'start-op','entry':self.request_entry,'scenario':self.scenario,
            'expected_manifest_ref':content_ref('run_manifest','run-1',{'run_id':'run-1'})}
    def state(self):
        return {table:[tuple(r) for r in self.db.execute('SELECT * FROM '+table+' ORDER BY 1')]
            for table in ('resource_runs','resource_operations','resource_bindings','resource_events','resource_meta')}
    def test_one_current_check_reserves_and_dispatches_exact_entry(self):
        req=self.request();self.assertEqual(authority.validate_request(req),req)
        with patch.object(self,'_check_start',wraps=self._check_start) as check:
            result=self._execute(req)
        self.assertEqual(check.call_count,1)
        self.assertEqual(result['operation']['entry'],self.entry)
        self.assertEqual(result['manifest_ref'],req['expected_manifest_ref'])
        self.assertTrue(result['operation']['dispatch_intended'])
        row=self.db.execute('SELECT * FROM resource_operations').fetchone()
        self.assertEqual(row['intended_at'],101);self.assertEqual(row['owner_epoch'],result['owner_epoch'])
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM resource_bindings').fetchone()[0],1)
    def test_duplicate_start_never_returns_another_dispatch_permission(self):
        req=self.request();self._execute(req);before=self.state()
        for request_id in ('start','retry'):
            with self.assertRaisesRegex(ResourceError,'DISPATCH_NOT_PROVABLY_NEW'):
                self._execute({**req,'request_id':request_id},now=102)
            self.assertEqual(self.state(),before)
    def test_failures_after_claim_or_reservation_roll_back_entire_start(self):
        for method in ('reserve','dispatch_intent'):
            before=self.state()
            with patch.object(ResourceBook,method,side_effect=ResourceError('INJECTED_FAILURE')):
                with self.assertRaisesRegex(ResourceError,'INJECTED_FAILURE'):self._execute(self.request())
            self.assertEqual(self.state(),before)
    def test_current_conditions_and_manifest_are_required_before_any_write(self):
        before=self.state()
        with patch.object(self,'_check_start',side_effect=ResourceError('CURRENT_UNAVAILABLE')):
            with self.assertRaisesRegex(ResourceError,'CURRENT_UNAVAILABLE'):self._execute(self.request())
        req=deepcopy(self.request());req['expected_manifest_ref']['digest']='f'*64
        with self.assertRaisesRegex(ResourceError,'MANIFEST_MISMATCH'):self._execute(req)
        self.assertEqual(self.state(),before)
    def test_retired_lease_cannot_reuse_an_already_started_operation(self):
        self._execute(self.request());before=self.state()
        req={**self.request(),'owner_id':'next-owner'}
        with self.assertRaisesRegex(ResourceError,'DISPATCH_NOT_PROVABLY_NEW'):self._execute(req,now=170)
        self.assertEqual(self.state(),before)
    def test_unplanned_entry_wrong_scenario_and_additional_fields_are_rejected(self):
        before=self.state();req=deepcopy(self.request());req['entry']['case_id']='unplanned'
        with self.assertRaisesRegex(ResourceError,'ENTRY_NOT_PLANNED'):self._execute(req)
        for fields in ({'scenario':'unsupported'},{'owner_epoch':1}):
            with self.assertRaises((ResourceError,ContractError)):authority.validate_request({**self.request(),**fields})
        self.assertEqual(self.state(),before)


if __name__=='__main__':unittest.main()
