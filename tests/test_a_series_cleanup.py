import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

BASE = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci'
spec = importlib.util.spec_from_file_location('a_series_cleanup', BASE / 'cleanup.py')
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)

PROD = 'unix:///var/run/docker.sock'
CI = cleanup.CI_DOCKER_SOCKET


class DockerCalls(unittest.TestCase):
    def test_prunes_are_refused_on_the_production_daemon(self):
        for args in (('volume', 'prune', '-f'), ('image', 'prune', '-f'), ('builder', 'prune', '-f'),
                     ('system', 'prune', '-af'), ('container', 'prune', '-f'), ('buildx', 'prune')):
            with patch.object(subprocess, 'run') as run:
                with self.assertRaises(cleanup.RefusedPrune):
                    cleanup.docker(PROD, *args)
                run.assert_not_called()

    def test_prunes_are_allowed_on_the_ci_socket_with_explicit_host(self):
        with patch.object(subprocess, 'run') as run:
            cleanup.docker(CI, 'image', 'prune', '-f')
        env = run.call_args.kwargs['env']
        self.assertEqual(env['DOCKER_HOST'], CI)

    def test_every_call_overrides_an_inherited_docker_host(self):
        with patch.dict(os.environ, {'DOCKER_HOST': 'tcp://elsewhere:2375'}):
            with patch.object(subprocess, 'run') as run:
                cleanup.docker(PROD, 'rm', '-f', 'abc')
        self.assertEqual(run.call_args.kwargs['env']['DOCKER_HOST'], PROD)

    def test_empty_host_is_rejected(self):
        with self.assertRaises(ValueError):
            cleanup.docker('', 'ps')

    def test_dry_run_prints_and_executes_nothing(self):
        with patch.object(subprocess, 'run') as run:
            cleanup.docker(CI, 'volume', 'prune', '-f', apply=False)
        run.assert_not_called()


class Orphans(unittest.TestCase):
    def inspect(self, labels, age_hours):
        created = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat().replace('+00:00', 'Z')
        return json.dumps([{'Created': created, 'Config': {'Labels': labels}}])

    def run_with(self, labels, age_hours, pid_alive=False):
        def fake(host, *args, capture=False, **_):
            return 'c1\n' if args[0] == 'ps' else self.inspect(labels, age_hours)
        with patch.object(cleanup, 'docker', side_effect=fake) as docker:
            found = list(cleanup.orphaned_containers(PROD, host_name='box', pid_exists=lambda pid: pid_alive))
        return found, docker

    def test_ps_is_filtered_by_the_ephemeral_label(self):
        _, docker = self.run_with({'com.jud.ephemeral': '1', 'com.jud.creator-host': 'box', 'com.jud.creator-pid': '9'}, 5)
        self.assertIn('label=com.jud.ephemeral', docker.call_args_list[0].args)

    def test_dead_creator_and_old_enough_is_returned(self):
        found, _ = self.run_with({'com.jud.ephemeral': '1', 'com.jud.creator-host': 'box', 'com.jud.creator-pid': '9'}, 5)
        self.assertEqual(found, ['c1'])

    def test_young_foreign_host_or_live_pid_is_kept(self):
        base = {'com.jud.ephemeral': '1', 'com.jud.creator-host': 'box', 'com.jud.creator-pid': '9'}
        self.assertEqual(self.run_with(base, 1)[0], [])
        self.assertEqual(self.run_with(dict(base, **{'com.jud.creator-host': 'other'}), 5)[0], [])
        self.assertEqual(self.run_with(base, 5, pid_alive=True)[0], [])
        self.assertEqual(self.run_with({'com.jud.creator-host': 'box', 'com.jud.creator-pid': '9'}, 5)[0], [])


class Main(unittest.TestCase):
    def test_missing_docker_host_exits(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit):
                cleanup.main([])

    def test_shell_wrapper_requires_explicit_docker_host_and_ci_socket_for_prune(self):
        text = (BASE / 'cache-cleanup.sh').read_text()
        self.assertIn(': "${DOCKER_HOST:?', text)
        self.assertIn('is not the CI daemon socket', text)

    def test_units_set_an_explicit_docker_host(self):
        for name in ('jud-ci-cleanup.service', 'jud-ci-cache-cleanup.service'):
            self.assertIn('Environment=DOCKER_HOST=', (BASE / name).read_text())


if __name__ == '__main__':
    unittest.main()
