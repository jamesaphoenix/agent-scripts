import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('successor', Path(__file__).with_name('successor.py'))
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)


class SuccessorTests(unittest.TestCase):
    def test_pilot_selection_and_bounded_render_argv_cannot_expand(self):
        command = M.render_command(','.join(M.PILOT))
        self.assertEqual(command[command.index('--ids') + 1], 'p9-after-a-bad-mock,t29-ratio-as-shares')
        self.assertEqual(command[command.index('--aspect') + 1], 'both')
        self.assertEqual(command[command.index('--jobs') + 1], '2')
        self.assertEqual(command[command.index('--workers') + 1], '4')
        with self.assertRaisesRegex(ValueError, 'Only fixed pilot'):
            M.render_command('all')

    def test_registry_requires_exact_pilot_and130_unique_marketing_ids(self):
        ids = M.PILOT + [f'p-test-{i}' for i in range(128)]
        config = {'pilotIds': M.PILOT, 'renderJobs': 2, 'workersPerRender': 4, 'nice': 10}
        with mock.patch.object(M, 'read', return_value=[{'id': x} for x in ids]):
            self.assertEqual(M.identities(config), ids)
            with self.assertRaisesRegex(ValueError, 'Fixed pilot'):
                M.identities({**config, 'pilotIds': ids[:3]})
        with mock.patch.object(M, 'read', return_value=[{'id': x} for x in ids[:-1] + [ids[-2]]]):
            with self.assertRaisesRegex(ValueError, 'Exact130'):
                M.identities(config)

    def test_missing_bulk_release_cannot_admit_bulk(self):
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(M, 'WORK', Path(temporary)):
            self.assertFalse(M.bulk_release('a' * 64, {}, 'render'))

    def test_bulk_release_binds_all_pilot_bytes_config_and_separate_paid_authority(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'pilot-qa-bulk-release.json'
            outputs = {f'{identity}:{aspect}': {'sha256': 'one-exact-value'} for identity in M.PILOT for aspect in M.ASPECTS}
            evidence = []
            for ordinal in range(4):
                p = root / f'note-{ordinal}.json'
                p.write_text('{}')
                evidence.append(M.ref(p))
            value = {'contract': 'root-marketing-successor-pilot-qa-release/v1', 'configSha256': 'a' * 64, 'pilotOutputs': outputs, 'pilotQualityAccepted': True, 'allowBulkRender': True, 'allowBulkPaidReview': False, 'reviewer': 'root', 'rationale': 'Four individual actual pilot judgements', 'evidence': evidence}
            path.write_text(json.dumps(value))
            with mock.patch.object(M, 'RUN', root), mock.patch.object(M, 'WORK', root):
                self.assertTrue(M.bulk_release('a' * 64, outputs, 'render'))
                with self.assertRaisesRegex(ValueError, 'Bulk paid review'):
                    M.bulk_release('a' * 64, outputs, 'review')
                with self.assertRaisesRegex(ValueError, 'exact current four'):
                    M.bulk_release('b' * 64, outputs, 'render')
                with self.assertRaisesRegex(ValueError, 'exact current four'):
                    M.bulk_release('a' * 64, {}, 'render')
                value['allowBulkPaidReview'] = True
                path.write_text(json.dumps(value))
                self.assertTrue(M.bulk_release('a' * 64, outputs, 'review'))
                (root / 'note-3.json').write_text('{"tamper":true}')
                with self.assertRaisesRegex(ValueError, 'Pinned evidence changed'):
                    M.bulk_release('a' * 64, outputs, 'review')

    def test_changed_config_refused_before_reload(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'config.json'
            path.write_text('{}')
            expected = M.sha(path)
            path.write_text('{"change":true}')
            with mock.patch.object(M, 'load') as load:
                with self.assertRaisesRegex(ValueError, 'Immutable successor config'):
                    M.checked(path, expected)
                load.assert_not_called()

    def test_existing_successor_checkpoint_refuses_restart_before_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'render-status.json').write_text('{"state":"uncertain"}')
            with mock.patch.object(M, 'WORK', root), mock.patch.object(M, 'load', return_value=({}, 'hash')), mock.patch.object(M.os, 'getpriority', return_value=10), mock.patch.object(M, 'render') as render, mock.patch('sys.argv', ['successor.py', '--config', str(root / 'config.json'), '--mode', 'render', '--execute']):
                with self.assertRaisesRegex(ValueError, 'Existing successor checkpoint'):
                    M.main()
                render.assert_not_called()

    def test_receipt_requires_current_full_output_and_frozen_graph(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'out').mkdir()
            for aspect in M.ASPECTS:
                p = root / f'out/p9-after-a-bad-mock-{aspect}.mp4'
                p.write_bytes(b'fixture-metadata-only-not-media')
                p.with_suffix('.render-receipt.json').write_text(json.dumps({'frozenBatchSourceSnapshot': 'frozen', 'output': {'path': str(p.relative_to(root)), 'bytes': p.stat().st_size, 'sha256': M.sha(p)}}))
            with mock.patch.object(M, 'APP', root):
                self.assertEqual(len(M.pair('p9-after-a-bad-mock', {'frozenSourceSnapshot': 'frozen'})), 2)
                with self.assertRaisesRegex(ValueError, 'another graph'):
                    M.pair('p9-after-a-bad-mock', {'frozenSourceSnapshot': 'changed'})
                (root / 'out/p9-after-a-bad-mock-wide.mp4').write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError, 'receipt bytes changed'):
                    M.pair('p9-after-a-bad-mock', {'frozenSourceSnapshot': 'frozen'})

    def test_new_installers_leave_shared_guards_and_old_checkpoints_alone(self):
        source = Path(__file__).with_name('install-launchd.sh').read_text()
        self.assertNotIn('render_tmpdir.py', source)
        self.assertNotIn('review_admission.py', source)
        self.assertLess(source.index('--check'), source.index('mkdir -p'))
        self.assertIn("'ProcessType': 'Standard'", source)
        self.assertIn("'LowPriorityIO': False", source)
        implementation = Path(__file__).with_name('successor.py').read_text()
        self.assertNotIn('marketing-revision-render-dispatch-status.json', implementation)
        self.assertNotIn('marketing-revision-review-dispatch-status.json', implementation)
        self.assertNotIn('marketing-review-dispatch.STOP', implementation)

    def test_model_argv_uses_only_selected_marketing_aspect_final_helper_and_shared_ledger(self):
        config = {'helpers': '/exact/final20'}
        with mock.patch.object(M, 'identities', return_value=M.PILOT):
            argv = M.review_command(config, M.PILOT[0], 'wide')
            self.assertEqual(argv[2], '/exact/final20/review-media.py')
            self.assertEqual(argv[argv.index('--max-requests') + 1], '1')
            self.assertEqual(argv[argv.index('--ids') + 1], M.PILOT[0])
            self.assertEqual(argv[argv.index('--aspect') + 1], 'wide')
            self.assertEqual(argv[argv.index('--ledger') + 1], str(M.RUN / 'ledger/media-review-ledger.json'))
            with self.assertRaisesRegex(ValueError, 'Unregistered marketing'):
                M.review_command(config, 'teaching:held', 'wide')

    def test_unlisted_source_drift_after_guard_wait_blocks_paid_child(self):
        # Protected inputs/config still pass, but an unlisted scene file changes
        # the actual SourceSnapshotter fingerprint while waiting for the slot.
        config = {'frozenSourceSnapshot': 'expected-entire-graph'}
        with mock.patch.object(M, 'checked') as protected, mock.patch.object(M.subprocess, 'check_output', return_value='changed-entire-graph\n') as snapshot, mock.patch.object(M, 'pair') as pair:
            with self.assertRaisesRegex(ValueError, 'Frozen successor graph changed'):
                M.paid_freshness(Path('/exact/config'), 'config-sha', config, M.PILOT[0], {})
            protected.assert_called_once()
            snapshot.assert_called_once()
            self.assertIn('SourceSnapshotter', snapshot.call_args.args[0][-1])
            self.assertEqual(snapshot.call_args.kwargs['cwd'], M.APP)
            pair.assert_not_called()
        source = Path(__file__).with_name('successor.py').read_text()
        block = source[source.index("with guard.admission(RUN, family='marketing'"):]
        self.assertLess(block.index('paid_freshness('), block.index('child = lease.popen('))


if __name__ == '__main__':
    unittest.main()
