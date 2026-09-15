"""固定client用containerの継続利用でも、各要求の主体・設定・同一性を検査する。"""
from copy import deepcopy
import threading
import unittest
from unittest.mock import Mock
from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError


class RunningClientTests(unittest.TestCase):
    def make(self):
        runtime=AuthorityRuntime.__new__(AuthorityRuntime)
        runtime.prefix='gah-authority-'+'a'*32
        runtime._client_mutex=threading.RLock();runtime._running_clients={};runtime._reusable_clients={}
        runtime._create=Mock();runtime._verify_config=Mock();runtime.remove_container=Mock()
        runtime.inspect=Mock(return_value={'Id':'same','RestartCount':0,'State':{
            'Running':True,'Pid':123,'Status':'running','OOMKilled':False,'StartedAt':'fixed'}})
        runtime.command=Mock(return_value=b'{"value":1}')
        return runtime
    def test_role_containers_are_distinct_and_each_request_has_a_fixed_new_client(self):
        runtime=self.make()
        for uid in (12004,12004,12003):
            self.assertEqual(runtime._running_client_once(uid,{'request':'fixed'}),{'value':1})
        self.assertEqual(runtime._create.call_count,2)
        self.assertEqual(runtime._verify_config.call_count,6)
        calls=[c.args[0] for c in runtime.command.call_args_list]
        self.assertEqual(sum(args[:2]==['container','start'] for args in calls),2)
        executions=[args for args in calls if args[:2]==['container','exec']]
        self.assertEqual(len(executions),3)
        for args,uid in zip(executions,(12004,12004,12003)):
            self.assertEqual(args[2:5],['--interactive','--user',f'{uid}:{uid}'])
            self.assertEqual(args[6:],['/usr/local/bin/python','-I','-B','/opt/gah/tools/authority_entry.py','client'])
        names=set(runtime._running_clients.values());self.assertEqual(len(names),2)
        runtime.close_clients();self.assertEqual(runtime._running_clients,{})
        self.assertEqual({c.args[0] for c in runtime.remove_container.call_args_list},names)
    def test_not_running_or_changed_config_rejects_before_request(self):
        for fault in ('stopped','config'):
            runtime=self.make();runtime._running_clients[12004]='existing'
            if fault=='stopped':runtime.inspect.return_value['State']['Running']=False
            else:runtime._verify_config.side_effect=AuthorityRuntimeError('CONFIG_CHANGED')
            with self.assertRaises(AuthorityRuntimeError):runtime._running_client_once(12004,{'request':'fixed'})
            runtime.command.assert_not_called();runtime.remove_container.assert_called_once_with('existing')
    def test_changed_identity_or_restart_after_request_rejects_response(self):
        for fault in ('Id','Pid','StartedAt','OOMKilled','RestartCount'):
            runtime=self.make();before=runtime.inspect.return_value;after=deepcopy(before)
            if fault in ('Id','RestartCount'):after[fault]='changed'
            else:after['State'][fault]='changed'
            runtime.inspect.side_effect=[before,after]
            with self.assertRaisesRegex(AuthorityRuntimeError,'CLIENT_FAILED'):
                runtime._running_client_once(12004,{'request':'fixed'})
            runtime.remove_container.assert_called_once();self.assertEqual(runtime._running_clients,{})
    def test_invalid_response_and_start_failure_reclaim_owned_container(self):
        for fault in ('output','start'):
            runtime=self.make()
            if fault=='output':runtime.command.return_value=b'not json'
            else:runtime.command.side_effect=AuthorityRuntimeError('START_FAILED')
            with self.assertRaises(AuthorityRuntimeError):runtime._running_client_once(12004,{'request':'fixed'})
            runtime.remove_container.assert_called_once();self.assertEqual(runtime._running_clients,{})
    def test_failed_cleanup_remains_owned_for_later_cleanup(self):
        runtime=self.make();runtime.command.side_effect=AuthorityRuntimeError('START_FAILED')
        runtime.remove_container.side_effect=AuthorityRuntimeError('STOP_UNCONFIRMED')
        with self.assertRaisesRegex(AuthorityRuntimeError,'STOP_UNCONFIRMED'):
            runtime._running_client_once(12004,{'request':'fixed'})
        self.assertEqual(len(runtime._running_clients),1)
        runtime.remove_container.side_effect=None;runtime.close_clients()
        self.assertEqual(runtime._running_clients,{})
    def test_unknown_identity_never_creates_container(self):
        runtime=self.make()
        for uid in (0,True,'12004',12005):
            with self.assertRaisesRegex(AuthorityRuntimeError,'IDENTITY_NOT_CONFIGURED'):
                runtime._running_client_once(uid,{'request':'fixed'})
        runtime._create.assert_not_called()


if __name__=='__main__':unittest.main()
