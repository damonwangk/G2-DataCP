"""MCAP source selection must never change the recording/output directory."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
import mcap_files


class McapFileTests(unittest.TestCase):
    def test_absolute_file_and_episode_outside_capture_root(self):
        with TemporaryDirectory() as tmp:
            root=Path(tmp).resolve(); capture=root/'capture';capture.mkdir()
            episode=root/'elsewhere'/'task_1';episode.mkdir(parents=True)
            file=episode/'raw.mcap';file.write_bytes(b'raw bytes')
            (episode/'raw.json').write_text(json.dumps({'label':'122episode','duration_s':2}))
            self.assertEqual(mcap_files.resolve_mcap(str(file),capture),file)
            self.assertEqual(mcap_files.resolve_mcap(str(episode),capture),file)
            found=mcap_files.listing(str(episode.parent),capture)
            self.assertEqual(found['files'][0]['id'],str(file))
            self.assertEqual(found['files'][0]['label'],'122episode')
            self.assertEqual(list(capture.iterdir()),[])
            self.assertEqual(file.read_bytes(),b'raw bytes')

    def test_browser_lists_files_and_folders_and_ignores_partials(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp).resolve();(p/'sub').mkdir();(p/'selected.mcap').touch();(p/'working.mcap.partial').touch()
            result=mcap_files.browse(str(p),p)
            self.assertEqual(result['folders'],[{'name':'sub','path':str(p/'sub')}])
            self.assertEqual([f['path'] for f in result['files']],[str(p/'selected.mcap')])
            with self.assertRaises(ValueError):mcap_files.resolve_mcap(str(p/'working.mcap.partial'),p)

    def test_legacy_names_and_aliases_still_work(self):
        with TemporaryDirectory() as tmp:
            p=Path(tmp).resolve();(p/'episode_1').mkdir();file=p/'episode_1/raw.mcap';file.touch()
            (p/'.episode_aliases.json').write_text(json.dumps({'old_episode':'episode_1'}))
            self.assertEqual(mcap_files.resolve_mcap('old_episode',p),file)
            with self.assertRaises(ValueError):mcap_files.resolve_mcap('../outside.mcap',p)


if __name__=='__main__':unittest.main()
