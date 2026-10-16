import importlib.util
import json
from pathlib import Path
import tempfile
import subprocess
import unittest
from contextlib import contextmanager
from unittest import mock

SPEC=importlib.util.spec_from_file_location('dispatch',Path(__file__).with_name('dispatch.py'))
module=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)

class ReadinessTests(unittest.TestCase):
    def test_revision_stop_saves_one_aspect_before_any_next_provider_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = root / 'revision/app'
            (app / 'data').mkdir(parents=True)
            (root / 'workflow').mkdir()
            (root / 'evidence').mkdir()
            (app / 'data/scripts.json').write_text(json.dumps([{'id': f'p{i}'} for i in range(130)]))
            config_path = root / 'config.json'
            config_path.write_text('{}')
            config = {'reviewEvidenceRoot': '/revision/review-cache', 'protectedSourceHashes': {}}
            status = root / 'workflow/status.json'
            calls = []

            def provider(command, **kwargs):
                calls.append(command)
                kwargs['stdout'].write(json.dumps({'id': 'p0', 'aspect': 'tall', 'state': 'completed_advisory'}) + '\n')
                kwargs['stdout'].flush()

                class Child:
                    pid = 123

                    def wait(self):
                        (root / 'workflow/marketing-revision-review-dispatch.STOP').write_text('operator pause')
                        return 0

                return Child()

            receipt = {'sha256': 'exact output', 'receiptSha256': 'exact receipt'}
            @contextmanager
            def admission(intent):
                class Lease:
                    popen = staticmethod(provider)
                yield Lease()
            with mock.patch.object(module, 'RUN', root), mock.patch.object(module, 'APP', app), \
                 mock.patch.object(module, 'STATUS', status), \
                 mock.patch.object(module, 'ready_pair', return_value={'tall': receipt, 'wide': receipt}), \
                 mock.patch.object(module, 'review_admission', side_effect=admission):
                with self.assertRaisesRegex(ValueError, 'Operator stop file'):
                    module.revision_queue((config_path, config, module.sha(config_path)))
            state = json.loads(status.read_text())
            self.assertEqual(state['phase'], 'halted')
            self.assertEqual(set(state['completedOutputs']), {'p0:tall'})
            self.assertNotIn('activeReviewPid', state)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][calls[0].index('--aspect') + 1], 'tall')
            self.assertEqual(calls[0][calls[0].index('--max-requests') + 1], '1')
            self.assertEqual(calls[0][calls[0].index('--evidence-root') + 1], '/revision/review-cache')

    def test_explicit_resume_archives_error_fields(self):
        state={'phase':'running','error':'old error','haltedAt':'old time','completedPairs':{}}
        module.archive_previous_error(state)
        self.assertNotIn('error',state)
        self.assertNotIn('haltedAt',state)
        self.assertEqual(state['historicalHalts'][0]['error'],'old error')
        module.archive_previous_error(state)
        self.assertEqual(len(state['historicalHalts']),1)

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
