"""Direct API seams with inactive/mock recorder; never enter FastAPI lifespan."""
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import app as server


class McapSourceApiTests(unittest.TestCase):
    def test_browse_list_and_contents_are_readonly_and_independent(self):
        with TemporaryDirectory() as td:
            root=Path(td).resolve();capture=root/'capture';capture.mkdir()
            other=root/'other';other.mkdir();file=other/'saved.mcap';file.write_bytes(b'unchanged')
            with patch.object(server,'data_dir',return_value=capture), \
                 patch.object(server,'motion_inventory',return_value={'motion_names':[]}) as inventory, \
                 patch.object(server.recorder,'start') as record, \
                 patch.object(server.receiver,'start') as receive:
                self.assertEqual(server.mcap_source_browse(str(other))['files'][0]['path'],str(file))
                self.assertEqual(server.mcap_source_files(str(other))['files'][0]['path'],str(file))
                self.assertEqual(server.mcap_source_contents(str(file)),{'motion_names':[]})
                self.assertEqual(inventory.call_args.args[0],file)
                record.assert_not_called();receive.assert_not_called()
            self.assertEqual(list(capture.iterdir()),[])
            self.assertEqual(file.read_bytes(),b'unchanged')

    def test_next_keeps_agent_task_name_when_request_omits_label(self):
        with patch.object(server.workflow.replay,'running',return_value=False), \
             patch.object(server,'robot_job',None), \
             patch.object(server.recorder,'next_episode',side_effect=lambda body:body):
            self.assertNotIn('label',server.record_next({}))
            self.assertEqual(server.record_next({'label':'122episode'})['label'],'122episode')


if __name__=='__main__':unittest.main()
