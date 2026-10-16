import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest

SPEC=importlib.util.spec_from_file_location('dispatch',Path(__file__).with_name('dispatch.py'))
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

class ReadinessTests(unittest.TestCase):
    def test_zsh_wrapper_resolves_its_runtime_directory_without_secrets(self):
        script=Path(__file__).with_name("run.sh")
        result=subprocess.check_output(["/bin/zsh",str(script),"--print-script-dir"],text=True)
        self.assertEqual(result.strip(),str(script.parent.resolve()))

    def test_waits_for_both_completed_receipts_and_refuses_corrupt_output(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'out').mkdir()
            for aspect in ['tall','wide']:
                media=root/f'out/p1-{aspect}.mp4';media.write_bytes(b'fixture only')
                receipt={'frozenBatchSourceSnapshot':module.FROZEN,'output':{
                    'path':f'out/p1-{aspect}.mp4','bytes':media.stat().st_size,'sha256':module.sha(media)}}
                media.with_suffix('.render-receipt.json').write_text(json.dumps(receipt))
                if aspect=='tall':self.assertIsNone(module.ready_pair(root,'p1'))
            self.assertEqual(set(module.ready_pair(root,'p1')),{'tall','wide'})
            (root/'out/p1-wide.mp4').write_bytes(b'changed output')
            with self.assertRaisesRegex(ValueError,'bytes'):
                module.ready_pair(root,'p1')

    def test_earlier_frozen_source_receipts_do_not_enter_queue(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'out').mkdir()
            media=root/'out/p1-tall.mp4';media.write_bytes(b'fixture')
            media.with_suffix('.render-receipt.json').write_text(json.dumps({
                'frozenBatchSourceSnapshot':'older source','output':{}}))
            self.assertIsNone(module.ready_pair(root,'p1'))

    def test_durable_status_has_no_budget_or_provider_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'status.json';value={'phase':'halted','publicationApproval':False}
            module.save(path,value)
            self.assertEqual(json.loads(path.read_text()),value)
            self.assertFalse(path.with_name('status.json.tmp').exists())

if __name__=='__main__':unittest.main()
