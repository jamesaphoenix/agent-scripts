import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

DIR = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci/rootless'
SCRIPT = DIR / 'setup-ci-rootless-docker.sh'


def stubs(tmp, groups='ci-runner docker'):
    """PATH stubs so --check runs without a rootless daemon or root."""
    bin_dir = Path(tmp) / 'bin'
    bin_dir.mkdir()
    for name, body in {
        'id': f'case "$*" in "-u ci-runner") echo 1001;; "-nG ci-runner") echo {groups};; *) /usr/bin/id "$@";; esac',
        'dpkg-query': 'exit 1', 'loginctl': 'exit 1', 'sysctl': 'echo 1', 'runuser': 'exit 1',
    }.items():
        (bin_dir / name).write_text('#!/bin/sh\n' + body + '\n')
        (bin_dir / name).chmod(0o755)
    return bin_dir


class Script(unittest.TestCase):
    def test_syntax(self):
        self.assertEqual(subprocess.run(['bash', '-n', str(SCRIPT)]).returncode, 0)

    @unittest.skipUnless(shutil.which('shellcheck'), 'shellcheck not installed')
    def test_shellcheck_clean(self):
        done = subprocess.run(['shellcheck', '-S', 'warning', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout)

    def run_script(self, *args, groups='ci-runner docker', env=None):
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = stubs(tmp, groups)
            sub = Path(tmp) / 'subuid'
            sub.write_text('ci-runner:165536:65536\n')
            full = {'PATH': f'{bin_dir}:/usr/bin:/bin', 'SUBUID_FILE': str(sub), 'SUBGID_FILE': str(sub),
                    'RUN_DIR': tmp, 'CI_SOCKET': '/run/user/1001/docker.sock', 'TEMPLATE': str(DIR.parent / 'runner.service.template')}
            full.update(env or {})
            return subprocess.run(['bash', str(SCRIPT), *args], capture_output=True, text=True, env=full)

    def test_check_reports_and_changes_nothing(self):
        done = self.run_script('--check')
        self.assertEqual(done.returncode, 1)  # not finished: reported as todo
        self.assertIn('[ok]   user ci-runner has uid 1001', done.stdout)
        self.assertIn('[ok]   subuid/subgid ranges for ci-runner', done.stdout)
        self.assertIn('[todo] packages:', done.stdout)
        self.assertIn('[todo] ci-runner removed from docker group', done.stdout)
        self.assertIn('[ok]   runner.service.template sets DOCKER_HOST', done.stdout)

    def test_check_passes_group_item_once_removed(self):
        done = self.run_script('--check', groups='ci-runner')
        self.assertIn('[ok]   ci-runner removed from docker group', done.stdout)

    def test_unknown_phase_and_option_are_rejected(self):
        self.assertEqual(self.run_script('--phase', 'nope').returncode, 2)
        self.assertEqual(self.run_script('--wat').returncode, 2)

    def test_changes_need_root(self):
        if os.geteuid() == 0:
            self.skipTest('running as root')
        self.assertEqual(self.run_script().returncode, 1)

    def test_every_mutation_is_dry_run_aware(self):
        text = SCRIPT.read_text()
        self.assertIn('do_it()', text)
        for dangerous in ('docker rm', 'docker system prune', 'volume prune', '--force', 'rm -rf /'):
            self.assertNotIn(dangerous, text)

    def test_group_is_removed_last_and_only_after_builders_and_template(self):
        text = SCRIPT.read_text()
        self.assertTrue(text.index('ALL_PHASES=(install runners builders services group)') > 0)
        group = text[text.index('phase_group()'):text.index('# ---- main')]
        self.assertIn('builder_up', group)
        self.assertIn('template_ok', group)
        self.assertIn('gpasswd -d', group)

    def test_template_has_no_docker_group_or_shared_daemon(self):
        template = (DIR.parent / 'runner.service.template').read_text()
        self.assertIn('Environment=DOCKER_HOST=unix:///run/user/1001/docker.sock', template)
        self.assertNotIn('SupplementaryGroups=docker', template)


if __name__ == '__main__':
    unittest.main()
