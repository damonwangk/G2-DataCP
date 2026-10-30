"""Offline API-contract tests; no service or robot SDK is contacted."""
import io
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import Mock

module = runpy.run_path(str(Path(__file__).with_name('datacp')))


class ContractTests(unittest.TestCase):
    def call(self, args):
        client = Mock()
        result = module['dispatch'](module['parser']().parse_args(args), client)
        return client, result

    def test_conversion_multiple_and_directory_sources(self):
        args = ['convert', '--repo-id', 'local/demo', '--task', '放进盒子']
        client, _ = self.call(args + ['--episode', '/data/A/episode_1', '--episode', '/data/A/episode_2'])
        client.call.assert_called_once_with('/api/conversion/start', {
            'repo_id': 'local/demo', 'task': '放进盒子',
            'episode_paths': ['/data/A/episode_1', '/data/A/episode_2']})
        client, _ = self.call(args + ['--source-dir', '/data/A'])
        self.assertEqual(client.call.call_args.args[1]['source_dir'], '/data/A')
        self.assertNotIn('episode_paths', client.call.call_args.args[1])

    def test_plain_dataset_folder_and_shared_root(self):
        client, _ = self.call(['convert','--name','抓取任务 01','--task','抓取','--source-dir','/data/data/A'])
        client.call.assert_called_once_with('/api/conversion/start', {
            'dataset_name':'抓取任务 01','task':'抓取','source_dir':'/data/data/A'})
        client, _ = self.call(['convert','--name','A','--task','抓取','--source-dir','/data/data/A','--output-dir','/data/train'])
        self.assertEqual(client.call.call_args.args[1]['output_dir'],'/data/train')
        client, _ = self.call(['datasets'])
        client.call.assert_called_once_with('/api/lerobot/datasets')
        client, _ = self.call(['lerobot-dir','/data/train'])
        client.call.assert_called_once_with('/api/workflow/settings',{'lerobot_root':'/data/train'})

    def test_replay_payloads_select_full_episode(self):
        client, _ = self.call(['replay', '--dataset', '/data/local/demo', '--episode', '2'])
        client.call.assert_called_once_with('/api/lerobot/replay/start', {'dataset': '/data/local/demo', 'episode': 2})
        client, _ = self.call(['mcap-replay', '--file', 'episode_1_20261030'])
        client.call.assert_called_once_with('/api/replay/start', {
            'file': 'episode_1_20261030', 'speed': 1., 'confirm_motion': True})

    def test_inspect_episodes_or_specific_episode(self):
        client, _ = self.call(['inspect', '--dataset', '/data/中文'])
        client.call.assert_called_once_with('/api/lerobot/episodes?dataset=%2Fdata%2F%E4%B8%AD%E6%96%87')
        client, _ = self.call(['inspect', '--dataset', '/data/中文', '--episode', '0'])
        client.call.assert_called_once_with('/api/lerobot/inspect', {'dataset': '/data/中文', 'episode': 0})

    def test_record_topics_forwarded_and_export_selection(self):
        client, _ = self.call(['record-start', '--topics', '/camera/head', '/joint_states'])
        client.call.assert_called_once_with('/api/record/start', {
            'label': 'episode', 'topics': ['/camera/head', '/joint_states']})
        client = Mock()
        client.call.return_value = {'archive_url': '/api/export/job/archive'}
        args = module['parser']().parse_args(['export-mcap', '--episode', '/data/A/episode_1', '--output', '/tmp/raw.tar'])
        module['dispatch'](args, client)
        client.call.assert_called_once_with('/api/export/prepare', {'episode_paths': ['/data/A/episode_1']})
        client.download.assert_called_once_with('/api/export/job/archive', '/tmp/raw.tar')
        client, _ = self.call(['export-lerobot', '--dataset', '/data/local/demo', '--output', '/tmp/demo.tar'])
        client.download.assert_called_once_with('/api/lerobot/export/archive?dataset=%2Fdata%2Flocal%2Fdemo', '/tmp/demo.tar')

    def test_http_json_and_download_bytes(self):
        client = module['Client']('http://robot:18769/')
        client.opener = Mock()
        client.opener.open.return_value = io.BytesIO(b'{"ok": true}')
        self.assertEqual(client.call('/api/record/stop', {}), {'ok': True})
        request = client.opener.open.call_args.args[0]
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), {})
        self.assertEqual(request.full_url, 'http://robot:18769/api/record/stop')
        payload = b'tar data' * 1024
        client.opener.open.return_value = io.BytesIO(payload)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'nested' / 'dataset.tar'
            result = client.download('/archive', target)
            self.assertEqual(target.read_bytes(), payload)
            self.assertEqual(result['bytes'], len(payload))


if __name__ == '__main__':
    unittest.main()
