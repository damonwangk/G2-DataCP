"""Single-segment rollback protects committed data, metadata and provenance."""
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from batch_io import begin, commit, recover, append_json, completed


class FakeMeta:
    total_episodes = 1
    video_keys = ['observation.images.head_color']

    def get_data_file_path(self, index):
        return Path(f'data/chunk-000/episode_{index:06d}.parquet')

    def get_video_file_path(self, index, key):
        return Path(f'videos/chunk-000/{key}/episode_{index:06d}.mp4')


class BatchIOTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'dataset'
        (self.root / 'meta').mkdir(parents=True)
        (self.root / 'provenance').mkdir()
        (self.root / 'meta/info.json').write_text('{"total_episodes": 1}\n')
        (self.root / 'meta/episodes.jsonl').write_text('{"episode_index":0}\n')
        (self.root / 'provenance/frames.jsonl').write_text('{"frame":0}\n')
        append_json(self.root / 'provenance/segments.jsonl', {'segment': 0})
        self.dataset = SimpleNamespace(root=self.root, meta=FakeMeta())
        self.committed = self.root / 'data/chunk-000/episode_000000.parquet'
        self.committed.parent.mkdir(parents=True)
        self.committed.write_bytes(b'previous committed parquet')
        self.original_meta = {p.name: p.read_bytes() for p in (self.root / 'meta').iterdir()}
        self.original_ledgers = {p.name: p.read_bytes() for p in (self.root / 'provenance').iterdir()}

    def mutate(self):
        (self.root / 'meta/info.json').write_text('{"total_episodes": 2}\n')
        (self.root / 'meta/new_metadata.json').write_text('must be removed')
        for name in ['frames.jsonl', 'segments.jsonl']:
            with (self.root / 'provenance' / name).open('ab') as stream:
                stream.write(b'{"uncommitted":true}\npartial')
        pending = json.loads((self.root / '.r2-transaction/pending.json').read_text())
        for name in pending['generated_files']:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'uncommitted')
        (self.root / 'images').mkdir()
        (self.root / 'images/intermediate.png').write_bytes(b'image')
        return pending

    def test_recovery_restores_metadata_and_truncates_both_ledgers(self):
        begin(self.dataset)
        pending = self.mutate()
        self.assertTrue(recover(self.root))
        self.assertEqual(self.original_meta, {p.name: p.read_bytes() for p in (self.root / 'meta').iterdir()})
        self.assertEqual(self.original_ledgers, {p.name: p.read_bytes() for p in (self.root / 'provenance').iterdir()})
        self.assertEqual(self.committed.read_bytes(), b'previous committed parquet')
        self.assertTrue(all(not (self.root / name).exists() for name in pending['generated_files']))
        self.assertFalse((self.root / 'images').exists())
        self.assertFalse(recover(self.root))

    def test_pending_transaction_names_next_episode_only(self):
        begin(self.dataset)
        pending = json.loads((self.root / '.r2-transaction/pending.json').read_text())
        self.assertEqual(pending['generated_files'], [
            'data/chunk-000/episode_000001.parquet',
            'videos/chunk-000/observation.images.head_color/episode_000001.mp4'])
        self.assertEqual(pending['ledger_sizes'], {name: len(data) for name, data in self.original_ledgers.items()})

    def test_rejects_pending_path_outside_dataset(self):
        begin(self.dataset)
        outside = self.root.parent / 'do_not_remove'
        outside.write_bytes(b'outside')
        marker = self.root / '.r2-transaction/pending.json'
        pending = json.loads(marker.read_text())
        pending['generated_files'] = ['../do_not_remove']
        marker.write_text(json.dumps(pending))
        with self.assertRaises(ValueError):
            recover(self.root)
        self.assertEqual(outside.read_bytes(), b'outside')
        self.assertEqual(self.committed.read_bytes(), b'previous committed parquet')

    def test_recovery_without_marker_retains_committed_files(self):
        begin(self.dataset)
        (self.root / '.r2-transaction/pending.json').unlink()
        self.assertFalse(recover(self.root))
        self.assertEqual(self.committed.read_bytes(), b'previous committed parquet')
        self.assertEqual(self.original_meta, {p.name: p.read_bytes() for p in (self.root / 'meta').iterdir()})

    def test_recovery_after_copy_failure_is_repeatable(self):
        begin(self.dataset)
        self.mutate()
        with patch('batch_io.shutil.copyfile', side_effect=OSError('simulated disk error')):
            with self.assertRaises(OSError):
                recover(self.root)
        self.assertTrue((self.root / '.r2-transaction/pending.json').exists())
        self.assertTrue(recover(self.root))
        self.assertEqual(self.original_meta, {p.name: p.read_bytes() for p in (self.root / 'meta').iterdir()})
        self.assertEqual(self.original_ledgers, {p.name: p.read_bytes() for p in (self.root / 'provenance').iterdir()})

    def test_recovery_marker_removed_before_snapshot_cleanup(self):
        begin(self.dataset)
        # No images here, so the patched cleanup is specifically the snapshot.
        (self.root / 'meta/info.json').write_text('{"total_episodes": 2}\n')
        with patch('batch_io.shutil.rmtree', side_effect=OSError('simulated recovery cleanup interruption')):
            with self.assertRaises(OSError):
                recover(self.root)
        self.assertFalse((self.root / '.r2-transaction/pending.json').exists())
        self.assertEqual(self.original_meta, {p.name: p.read_bytes() for p in (self.root / 'meta').iterdir()})
        self.assertFalse(recover(self.root))
        self.assertEqual(self.original_meta, {p.name: p.read_bytes() for p in (self.root / 'meta').iterdir()})

    def test_commit_marker_is_removed_before_snapshot_cleanup(self):
        begin(self.dataset)
        (self.root / 'meta/info.json').write_text('{"total_episodes": 2}\n')
        with patch('batch_io.shutil.rmtree', side_effect=OSError('simulated cleanup interruption')):
            with self.assertRaises(OSError):
                commit(self.root)
        self.assertFalse((self.root / '.r2-transaction/pending.json').exists())
        # An interruption after commit must never roll valid metadata backward.
        self.assertFalse(recover(self.root))
        self.assertEqual(json.loads((self.root / 'meta/info.json').read_text())['total_episodes'], 2)

    def test_completed_reads_only_committed_ledger(self):
        self.assertEqual(completed(self.root), [{'segment': 0}])
        begin(self.dataset)
        self.mutate()
        recover(self.root)
        self.assertEqual(completed(self.root), [{'segment': 0}])


class EmptyDatasetResumeTests(unittest.TestCase):
    def test_owned_empty_output_can_resume_without_loading_parquet_or_hub(self):
        from types import SimpleNamespace
        import convert_r2
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            source = parent / 'raw'
            source.mkdir()
            output = parent / 'dataset'
            args = SimpleNamespace(input=[str(source)], inspect=False, output=str(output),
                repo_id='local/recovery_test', task='recovery test only', tasks_json=None,
                fps=30, image_size=32, media='image', max_offset_ms=20.0,
                max_camera_age_ms=50.0, max_gap_ms=50.0, resume=False, skip_errors=False)
            # Abort after official dataset creation but before any segment save.
            with patch.object(convert_r2, 'discover', return_value=[source]), \
                 patch.object(convert_r2, 'make_plan', side_effect=RuntimeError('planned preflight failure')):
                with self.assertRaisesRegex(RuntimeError, 'planned preflight failure'):
                    convert_r2.convert(args)
                self.assertEqual(json.loads((output / 'meta/info.json').read_text())['total_episodes'], 0)
                args.resume = True
                with self.assertRaisesRegex(RuntimeError, 'planned preflight failure'):
                    convert_r2.convert(args)


if __name__ == '__main__':
    unittest.main()
