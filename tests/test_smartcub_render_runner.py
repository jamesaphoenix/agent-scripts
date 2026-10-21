import importlib.util
import json
import os
from pathlib import Path
import tempfile
import shutil
import subprocess
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('render_runner', ROOT / 'media-quality/render-batch/runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node') and shutil.which('plutil'), 'requires macOS and Node')
    def test_launchd_priority_allows_the_existing_renderer_to_select_nice_ten(self):
        if os.getpriority(os.PRIO_PROCESS, 0) != 0:
            self.skipTest('requires a normal-priority test parent')
        plist = ROOT / 'launchd-tasks/plists/com.jud.smartcub-render-20261021.plist'
        job = json.loads(subprocess.check_output(['plutil', '-convert', 'json', '-o', '-', str(plist)]))
        # The frozen marketing helper adds nice 10; the existing Node renderer
        # sets its absolute priority to 10. An inherited launchd nice 10 would
        # make that an unauthorised priority increase from 20 back to 10.
        result = subprocess.run(['nice', '-n', str(job.get('Nice', 0) + 10), 'node', '-e',
            "const os=require('node:os');os.setPriority(0,10);console.log(os.getPriority(0))"],
            capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), '10')

    def test_provider_and_oauth_credentials_are_not_inherited(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'test', 'OP_SERVICE_ACCOUNT_TOKEN': 'test',
                                     'FAL_KEY': 'test', 'GEMINI_API_KEY': 'test'}):
            env = runner.environment()
        self.assertNotIn('OP_SERVICE_ACCOUNT_TOKEN', env)
        self.assertFalse(any(key.endswith('_KEY') for key in env))
        self.assertIn('HOME', env)

    def test_interrupted_uncommitted_outputs_are_quarantined_without_touching_completed_outputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            folder = w / 'teaching-successor'
            folder.mkdir()
            partial, complete = folder / 'partial.mp4', folder / 'complete.mp4'
            partial.write_bytes(b'partial')
            complete.write_bytes(b'complete')
            complete.with_suffix('.receipt.json').write_text('{}')
            rows = runner.recover_partial(w, w / 'quarantine')
            self.assertEqual(len(rows), 1)
            self.assertFalse(partial.exists())
            self.assertEqual(complete.read_bytes(), b'complete')
            self.assertEqual(Path(rows[0]['to']).read_bytes(), b'partial')

    def test_committed_missing_receipt_stops_instead_of_destroying_checkpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            p = w / 'teaching-successor/committed.mp4'
            p.parent.mkdir()
            p.write_bytes(b'committed')
            runner.atomic(w / 'teaching-join-progress.json', {'jobs': {'one': {'output': str(p)}}})
            with self.assertRaisesRegex(RuntimeError, 'lost its receipt'):
                runner.recover_partial(w, w / 'quarantine')
            self.assertTrue(p.exists())

    def config(self, w):
        baseline = w / 'baseline'
        baseline.mkdir()
        current = w / 'current'
        current.symlink_to(baseline, target_is_directory=True)
        return {'workflow': str(w), 'runtime': str(w / 'runtime'), 'pins': {},
                'current': str(current), 'baselineDirectory': str(baseline.resolve()), 'steps': {}}

    def test_changed_frozen_helper_prevents_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            c = self.config(w)
            helper = w / 'helper.py'
            helper.write_text('old')
            c['pins'][str(helper)] = runner.sha(helper)
            helper.write_text('changed')
            with self.assertRaisesRegex(RuntimeError, 'Frozen input'):
                runner.Controller(c).validate()

    def test_changed_delivery_pointer_prevents_mixed_baseline(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            c = self.config(w)
            Path(c['current']).unlink()
            Path(c['current']).symlink_to(w / 'another')
            with self.assertRaisesRegex(RuntimeError, 'pointer changed'):
                runner.Controller(c).validate()

    def test_producer_failure_stops_waiting_scanner_but_other_producer_finishes(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            c = self.config(w)
            marker = w / 'finished'
            c['steps'] = {
                'marketing': {'argv': [sys.executable, '-c',
                    'import time,pathlib;time.sleep(.1);pathlib.Path(' + repr(str(marker)) + ').touch()']},
                'teaching': {'argv': [sys.executable, '-c', 'raise SystemExit(4)']},
                'scans': {'argv': [sys.executable, '-c', 'import time;time.sleep(30)']},
                'joins': {'argv': [sys.executable, '-c', 'raise SystemExit(0)'], 'after': ['teaching']}}
            controller = runner.Controller(c, tick=.02)
            with patch('builtins.print'):
                controller.run()
            self.assertTrue(marker.exists())
            status = runner.read(controller.status_file)
            self.assertEqual(status['state'], 'failed_needs_attention')
            self.assertEqual(status['jobs']['marketing']['state'], 'complete')
            self.assertEqual(status['jobs']['joins']['state'], 'skipped')
            self.assertEqual(Path(c['current']).resolve(), Path(c['baselineDirectory']))

    def test_success_does_not_claim_editorial_approval_or_change_pointer(self):
        import sys
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            c = self.config(w)
            c['steps'] = {'marketing': {'argv': [sys.executable, '-c', 'pass']}}
            controller = runner.Controller(c, tick=.02)
            with patch.object(runner, 'export_candidates', return_value=str(w / 'rendered-videos')), patch('builtins.print'):
                controller.run()
            status = runner.read(controller.status_file)
            self.assertEqual(status['state'], 'rendered_scanned_pending_editorial_review')
            self.assertEqual(status['approval'], 'pending_editorial_review')
            self.assertEqual(status['paidRequests'], 0)
            self.assertEqual(Path(c['current']).resolve(), Path(c['baselineDirectory']))

    def test_stale_worker_pid_reuse_does_not_signal_unrelated_process(self):
        with patch.object(runner, 'birth', return_value='different-start'), patch.object(os, 'killpg') as kill:
            runner.stop_owned({'pid': 12345, 'birth': 'old-start'})
        kill.assert_not_called()

    def test_interrupted_marketing_clone_can_restart_without_overwriting_committed_media(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            target = w / 'source/marketing-partial'
            target.mkdir(parents=True)
            (target / 'unfinished').write_text('incomplete clone')
            runner.atomic(w / 'marketing-render-progress.json', {
                'roots': {'original': {'state': 'rendering', 'target': str(target)}}, 'outputs': {}})
            runner.recover_partial(w, w / 'quarantine')
            self.assertFalse(target.exists())
            self.assertEqual(runner.read(w / 'marketing-render-progress.json')['roots'], {})
            self.assertEqual((w / 'quarantine/source/marketing-partial/unfinished').read_text(), 'incomplete clone')

    def test_export_preserves_restrictions_and_never_copies_old_approval_as_new(self):
        with tempfile.TemporaryDirectory() as tmp:
            w = Path(tmp)
            c = self.config(w)
            c.update(total=1, baselineManifest=str(w / 'baseline/manifest.json'))
            source = w / 'baseline/clip.mp4'
            source.write_bytes(b'verified-media')
            original = {'id': 'one', 'kind': 'cta', 'aspect': 'tall', 'cta_id': 'cta-sc-01',
                        'path': 'cta/tall/one.mp4', 'distribution_restrictions': {'hold': 'date-sensitive'},
                        'checks': {'layout': 'pass'}, 'reviewed_at': 'old-date'}
            runner.atomic(c['baselineManifest'], {'generation': 'baseline', 'videos': [original]})
            runner.atomic(w / 'impact-map-v3.json', {'outputs': [{'id': 'one', 'kind': 'cta', 'aspect': 'tall',
                'ctaId': 'cta-sc-01', 'affected': False, 'currentPath': str(source), 'currentSha256': runner.sha(source)}]})
            runner.atomic(w / 'marketing-render-progress.json', {'outputs': {}})
            runner.atomic(w / 'teaching-join-progress.json', {'jobs': {}})
            runner.atomic(w / 'source/james/data/ctas.json', [{'id': 'cta-sc-01'}])
            output = Path(runner.export_candidates(c))
            manifest = runner.read(output / 'manifest.json')
            row = manifest['videos'][0]
            self.assertFalse(manifest['approved_for_launch'])
            self.assertEqual(row['checks'], {})
            self.assertNotIn('reviewed_at', row)
            self.assertEqual(row['distribution_restrictions'], original['distribution_restrictions'])
            self.assertEqual((output / row['path']).read_bytes(), b'verified-media')


if __name__ == '__main__':
    unittest.main()
