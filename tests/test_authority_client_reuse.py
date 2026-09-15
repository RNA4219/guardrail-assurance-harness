"""再利用clientの主体分離・各回照合・失敗時回収を検査する。Dockerは起動しない。"""
import threading
import unittest
from unittest.mock import Mock
from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError


class AuthorityClientReuseTests(unittest.TestCase):
    def make(self):
        runtime=AuthorityRuntime.__new__(AuthorityRuntime)
        runtime.prefix='gah-authority-'+'a'*32
        runtime._client_mutex=threading.RLock();runtime._reusable_clients={}
        runtime._create=Mock();runtime._verify_config=Mock();runtime.remove_container=Mock()
        runtime.inspect=Mock(return_value={'Id':'same','State':{'Running':False,'Pid':0,'Status':'exited','ExitCode':0}})
        runtime.command=Mock(return_value=b'{"value":1}')
        return runtime

    def test_each_request_rechecks_the_stopped_client_and_separates_identities(self):
        runtime=self.make()
        for uid in (12004,12004,12003):
            self.assertEqual(runtime._reused_client_once(uid,{'request':'fixed'}),{'value':1})
        self.assertEqual(runtime._create.call_count,2)
        self.assertEqual(runtime._verify_config.call_count,6)
        self.assertEqual(runtime.command.call_count,3)
        names=set(runtime._reusable_clients.values());self.assertEqual(len(names),2)
        runtime.close_clients()
        self.assertEqual({call.args[0] for call in runtime.remove_container.call_args_list},names)
        self.assertEqual(runtime._reusable_clients,{})

    def test_active_or_changed_client_is_rejected_and_removed(self):
        for failure in ('active','config'):
            with self.subTest(failure=failure):
                runtime=self.make()
                if failure=='active':runtime.inspect.return_value['State']['Running']=True
                else:runtime._verify_config.side_effect=AuthorityRuntimeError('CONFIG_CHANGED')
                with self.assertRaises(AuthorityRuntimeError):runtime._reused_client_once(12004,{'request':'fixed'})
                runtime.command.assert_not_called();runtime.remove_container.assert_called_once()
                self.assertEqual(runtime._reusable_clients,{})

    def test_invalid_response_is_not_reused(self):
        runtime=self.make();runtime.command.return_value=b'not json'
        with self.assertRaisesRegex(AuthorityRuntimeError,'CLIENT_RESPONSE_INVALID'):
            runtime._reused_client_once(12004,{'request':'fixed'})
        runtime.remove_container.assert_called_once();self.assertFalse(runtime._reusable_clients)

    def test_cleanup_keeps_failed_ownership_for_retry_and_continues_other_clients(self):
        runtime=self.make();runtime._reusable_clients={(12004,'client'):'first',(12003,'client'):'second'}
        runtime.remove_container.side_effect=[AuthorityRuntimeError('STOP_UNCONFIRMED'),None]
        with self.assertRaisesRegex(AuthorityRuntimeError,'STOP_UNCONFIRMED'):runtime.close_clients()
        self.assertEqual(runtime._reusable_clients,{(12004,'client'):'first'})
        self.assertEqual(runtime.remove_container.call_count,2)


if __name__=='__main__':unittest.main()
