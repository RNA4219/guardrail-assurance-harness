"""別CLIの所有記録を取り込んだ回収と、回収漏れの拒否を検査する。"""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock
from tools.authority_runtime import AuthorityRuntime, AuthorityRuntimeError


class AuthorityCleanupRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.runtime = AuthorityRuntime.__new__(AuthorityRuntime)
        r = self.runtime
        r.database_mode = 'default'
        r.folder = Path(self.temp.name)
        r.prefix = 'gah-authority-' + 'a' * 32
        r.lock = {'image_id': 'sha256:' + 'b' * 64}
        self.first = r.prefix + '-broker'
        self.second = r.prefix + '-client-' + 'c' * 32
        r.state = {'prefix': r.prefix, 'image_id': r.lock['image_id'], 'containers': [self.first]}
        self.latest = {**r.state, 'containers': [self.first, self.second]}
        self.write(self.latest)
        r.remove_container = Mock()
        r.command = Mock(return_value=b'')

    def write(self, value):
        (self.runtime.folder / 'deployment.json').write_text(json.dumps(value), encoding='utf8')

    def test_stale_parent_reclaims_client_added_by_another_cli(self):
        self.runtime.cleanup()
        self.assertEqual([x.args[0] for x in self.runtime.remove_container.call_args_list], [self.first, self.second])
        self.assertEqual(self.runtime.state['containers'], [self.first, self.second])
        self.assertEqual(self.runtime.command.call_args.args[0][-2:], ['--format', '{{.Names}}'])

    def test_changed_ownership_or_malformed_state_is_rejected_before_removal(self):
        for kind in ('prefix', 'image', 'foreign_name', 'duplicate'):
            with self.subTest(kind=kind):
                value = deepcopy(self.latest)
                if kind == 'prefix': value['prefix'] = 'gah-authority-' + 'd' * 32
                elif kind == 'image': value['image_id'] = 'sha256:' + 'e' * 64
                elif kind == 'foreign_name': value['containers'].append('unrelated-container')
                else: value['containers'].append(self.second)
                self.write(value)
                with self.assertRaisesRegex(AuthorityRuntimeError, 'DEPLOYMENT_CONFLICT'):
                    self.runtime.cleanup()
                self.runtime.remove_container.assert_not_called()

    def test_failed_removal_retains_ownership_and_continues_other_removals(self):
        r = self.runtime
        r.remove_container.side_effect = [AuthorityRuntimeError('STOP_UNCONFIRMED'), None]
        with self.assertRaisesRegex(AuthorityRuntimeError, 'STOP_UNCONFIRMED'):
            r.cleanup()
        self.assertEqual(r.remove_container.call_count, 2)
        self.assertEqual(json.loads((r.folder / 'deployment.json').read_text())['containers'], [self.first, self.second])
        r.remove_container.side_effect = None
        r.cleanup()
        self.assertEqual(r.remove_container.call_count, 4)

    def test_remaining_owned_container_cannot_be_reported_as_clean(self):
        self.runtime.command.return_value = (self.second + '\n').encode()
        with self.assertRaisesRegex(AuthorityRuntimeError, 'CLEANUP_INCOMPLETE'):
            self.runtime.cleanup()

    def test_missing_current_deployment_cannot_use_stale_memory(self):
        (self.runtime.folder / 'deployment.json').unlink()
        with self.assertRaisesRegex(AuthorityRuntimeError, 'DEPLOYMENT_CONFLICT'):
            self.runtime.cleanup()
        self.runtime.remove_container.assert_not_called()


if __name__ == '__main__':
    unittest.main()
