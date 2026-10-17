import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class ReviewLauncherTests(unittest.TestCase):
    def module(self):
        spec = importlib.util.spec_from_file_location('review40_launcher', Path(__file__).with_name('launch.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def fixture(self, module, root):
        script = root / 'review-corrected-batch.py'
        script.write_text('never executed')
        plan = root / 'review-plan.json'
        plan.write_text('{}')
        ref = lambda path: {'path': str(path), 'bytes': path.stat().st_size, 'sha256': module.sha(path)}
        config = root / 'approved-review-batch.json'
        config.write_text(json.dumps({'contract': 'smart-cub-corrected40-review-launch/v1', 'script': ref(script), 'plan': ref(plan), 'status': str(root / 'review-status.json'), 'stopPath': str(root / 'review.STOP')}))
        return config, script, plan

    def test_missing_binding_never_executes_or_opens_paid_domain(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary, mock.patch.object(module, 'CONFIG', Path(temporary) / 'missing.json'), mock.patch.object(module.socket, 'gethostname', return_value='JamessMacStudio.fritz.box'), mock.patch.object(module.os, 'execv') as execute:
            with self.assertRaises(FileNotFoundError):
                module.command()
            execute.assert_not_called()

    def test_exact_review_only_argv_and_changed_plan_fail_closed(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, script, plan = self.fixture(module, root)
            with mock.patch.object(module, 'RUN', root), mock.patch.object(module, 'CONFIG', config), mock.patch.object(module.socket, 'gethostname', return_value='JamessMacStudio.fritz.box'):
                self.assertEqual(module.command(), [str(module.PYTHON), '-B', str(script), 'run', '--plan', str(plan), '--plan-sha256', module.sha(plan), '--status', str(root / 'review-status.json'), '--stop-path', str(root / 'review.STOP'), '--execute'])
                plan.write_text('{"changed":true}')
                with self.assertRaisesRegex(ValueError, 'Pinned bytes'):
                    module.command()

    def test_existing_status_forbids_automatic_paid_restart(self):
        module = self.module()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.fixture(module, root)
            (root / 'review-status.json').write_text('{"state":"uncertain"}')
            with mock.patch.object(module, 'RUN', root), mock.patch.object(module, 'CONFIG', config), mock.patch.object(module.socket, 'gethostname', return_value='JamessMacStudio.fritz.box'):
                with self.assertRaisesRegex(ValueError, 'Existing review status'):
                    module.command()

    def test_nonstudio_or_escaping_stop_path_refused(self):
        module = self.module()
        with mock.patch.object(module.socket, 'gethostname', return_value='MacBook-Pro'):
            with self.assertRaisesRegex(ValueError, 'Mac Studio'):
                module.command()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config, _, _ = self.fixture(module, root)
            value = json.loads(config.read_text())
            value['stopPath'] = '/outside/review.STOP'
            config.write_text(json.dumps(value))
            with mock.patch.object(module, 'RUN', root), mock.patch.object(module, 'CONFIG', config), mock.patch.object(module.socket, 'gethostname', return_value='JamessMacStudio.fritz.box'):
                with self.assertRaisesRegex(ValueError, 'Status and stop'):
                    module.command()

    def test_check_is_metadata_only_and_wrong_nice_cannot_execute(self):
        module = self.module()
        with mock.patch.object(module, 'command', return_value=['not executed']), mock.patch.object(module.os, 'execv') as execute, mock.patch.object(module.sys, 'argv', ['launch.py', '--check']):
            module.main()
            execute.assert_not_called()
        with mock.patch.object(module, 'command', return_value=['not executed']), mock.patch.object(module.os, 'getpriority', return_value=0), mock.patch.object(module.os, 'execv') as execute, mock.patch.object(module.sys, 'argv', ['launch.py', '--execute']):
            with self.assertRaisesRegex(ValueError, 'nice10'):
                module.main()
            execute.assert_not_called()

    def test_installer_cannot_copy_or_replace_shared_policies(self):
        source = Path(__file__).with_name('install-launchd.sh').read_text()
        self.assertNotIn('render_tmpdir.py', source)
        self.assertNotIn('review_admission.py', source)
        self.assertLess(source.index('launch.py" --check'), source.index('mkdir -p'))
        self.assertIn("'ProcessType': 'Standard'", source)
        self.assertIn("'LowPriorityIO': False", source)
        self.assertIn("'RunAtLoad': False", source)
        self.assertIn("'KeepAlive': False", source)

    def test_secret_loading_follows_metadata_gate_and_uses_existing_bounded_shim(self):
        source = Path(__file__).with_name('run.sh').read_text()
        self.assertLess(source.index('launch.py" --check'), source.index('source "$SCRIPT_DIR/op-service-account-env.sh"'))
        self.assertIn('getconf DARWIN_USER_TEMP_DIR', source)
        self.assertIn('OP_LOAD_DESKTOP_APP_SETTINGS=false', source)
        self.assertNotIn('export HOME=', source)
        self.assertNotIn('ANTHROPIC_API_KEY', source)
        self.assertNotIn('set -x', source)


if __name__ == '__main__':
    unittest.main()
