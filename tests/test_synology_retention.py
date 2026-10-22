import importlib.util
from pathlib import Path
import tempfile
import unittest
import json
import fcntl
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('nas_retention', Path(__file__).resolve().parents[1] / 'agent-loops/synology-retention/retention.py')
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)

REPO = 'europe-west3-docker.pkg.dev/tracelearn-prod/trace-learn/api'
NOW = 2_000_000


def release(name, age=200_000):
    return {'path': name, 'sourceSha': name, 'images': {'API_IMAGE': REPO + '@sha256:' + name}, 'modified': NOW - age}


class RetentionTests(unittest.TestCase):
    def snapshot(self):
        rows = [release(str(i)) for i in range(6)]
        return {'projects': {'trace-learn-staging': {'current': rows[-1], 'releases': rows}}, 'images': [], 'containers': []}

    def test_bootstrap_cannot_discard_unverified_rollback_history(self):
        snapshot = self.snapshot()
        plan, history = retention.plan(snapshot, {}, NOW)
        self.assertEqual(plan['releasePaths'], [])
        self.assertEqual(plan['bootstrapProjects'], ['trace-learn-staging'])
        self.assertEqual(len(history['trace-learn-staging']), 1)

    def test_keeps_current_and_two_observed_healthy_rollbacks(self):
        snapshot = self.snapshot()
        history = {'trace-learn-staging': snapshot['projects']['trace-learn-staging']['releases'][:5]}
        plan, _ = retention.plan(snapshot, history, NOW)
        self.assertEqual(plan['releasePaths'], ['0', '1', '2'])

    def test_recent_failed_attempt_is_retained_for_48_hours(self):
        snapshot = self.snapshot()
        snapshot['projects']['trace-learn-staging']['releases'][0]['modified'] = NOW - 60
        history = {'trace-learn-staging': snapshot['projects']['trace-learn-staging']['releases'][1:5]}
        plan, _ = retention.plan(snapshot, history, NOW)
        self.assertNotIn('0', plan['releasePaths'])

    def test_duplicate_deploy_does_not_replace_a_distinct_rollback(self):
        snapshot = self.snapshot()
        old = release('3'); duplicate = dict(old, path='3-copy')
        snapshot['projects']['trace-learn-staging']['releases'].append(duplicate)
        history = {'trace-learn-staging': [release('2'), old, duplicate]}
        plan, _ = retention.plan(snapshot, history, NOW)
        self.assertNotIn('2', plan['releasePaths'])
        self.assertNotIn('3-copy', plan['releasePaths'])
        self.assertIn('3', plan['releasePaths'])

    def test_any_container_and_any_retained_release_protect_images(self):
        snapshot = self.snapshot()
        snapshot['images'] = [self.image('old', '0'), self.image('running', '1'), self.image('rollback', '4'), self.image('shared', '2', extra='other/project:latest'), self.image('dangling', None)]
        snapshot['containers'] = [{'Image': 'running'}]  # Includes stopped and unrelated containers.
        history = {'trace-learn-staging': [release('3'), release('4')]}
        plan, _ = retention.plan(snapshot, history, NOW)
        self.assertEqual(plan['imageIds'], ['old'])

    def test_recent_images_and_other_applications_are_never_deleted(self):
        snapshot = self.snapshot()
        snapshot['images'] = [dict(self.image('new', '0'), created=NOW - 1), self.image('unknown', None, extra='solar-roi-api:latest')]
        plan, _ = retention.plan(snapshot, {'trace-learn-staging': [release('4')]}, NOW)
        self.assertEqual(plan['imageIds'], [])

    def test_old_unreferenced_dangling_images_are_bounded_and_container_safe(self):
        snapshot = self.snapshot()
        snapshot['images'] = [dict(self.image('old-dangling', None), created=NOW - retention.DANGLING_AGE - 1),
                              dict(self.image('recent-dangling', None), created=NOW - retention.DANGLING_AGE + 1),
                              dict(self.image('running-dangling', None), created=NOW - retention.DANGLING_AGE - 1)]
        snapshot['containers'] = [{'Image': 'running-dangling'}]
        plan, _ = retention.plan(snapshot, {'trace-learn-staging': [release('4')]}, NOW)
        self.assertEqual(plan['imageIds'], ['old-dangling'])

    def test_octospark_ml_release_image_is_accepted(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            folder = root / 'octospark-prod/releases/a'; folder.mkdir(parents=True)
            sha = 'a' * 40
            digest = 'b' * 64
            images = {key: f'europe-west3-docker.pkg.dev/octospark-prod/tx-agent-kit/{repo}@sha256:{digest}'
                      for key, repo in [('API_IMAGE', 'api'), ('WORKER_IMAGE', 'worker'), ('ML_IMAGE', 'ml')]}
            (folder / 'release.json').write_text(json.dumps({'project': 'octospark-prod', 'sourceSha': sha, 'images': images}))
            with patch.object(retention, 'ROOT', root):
                self.assertEqual(retention.read_release('octospark-prod', folder)['images'], images)

    def test_stopped_container_bind_mount_protects_old_private_release_files(self):
        snapshot = self.snapshot()
        snapshot['containers'] = [{'Image': 'dependency', 'mountSources': ['0/cloudsql/key.json']}]
        snapshot['images'] = [self.image('old', '0')]
        plan, _ = retention.plan(snapshot, {'trace-learn-staging': [release('3'), release('4')]}, NOW)
        self.assertNotIn('0', plan['releasePaths'])
        self.assertEqual(plan['imageIds'], [])

    def test_bootstrap_of_sibling_environment_protects_shared_repository(self):
        snapshot = self.snapshot()
        snapshot['projects']['trace-learn-prod'] = {'current': release('prod'), 'releases': [release('prod')]}
        snapshot['images'] = [self.image('old', '0')]
        plan, _ = retention.plan(snapshot, {'trace-learn-staging': [release('4')]}, NOW)
        self.assertEqual(plan['imageIds'], [])

    def test_symlink_release_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); folder = root / 'release'; folder.mkdir()
            (folder / 'escape').symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                retention.tree_modified(folder)

    def test_effective_path_cannot_escape_release_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve(); releases = root / 'releases'; releases.mkdir()
            outside = root / 'outside'; outside.mkdir(); (releases / 'bad').symlink_to(outside)
            with self.assertRaises(ValueError):
                retention.checked_child(releases, releases / 'bad')
            with self.assertRaises(ValueError):
                retention.checked_child(releases, outside)

    def test_nas_python_accepts_nanosecond_docker_timestamp(self):
        self.assertEqual(retention.created_at('2026-10-13T11:15:24.488419252Z'), retention.created_at('2026-10-13T11:15:24.488419Z'))

    def test_apply_deletes_only_eligible_release_and_uses_nonforce_image_removal(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for project in retention.PROJECTS:
                (root / project).mkdir()
            state = root / 'maintenance'; state.mkdir(); (state / 'cleanup-enabled').touch()
            snapshot = self.snapshot()
            folders = root / 'trace-learn-staging/releases'; folders.mkdir()
            for record in snapshot['projects']['trace-learn-staging']['releases']:
                folder = folders / record['path']; folder.mkdir(); (folder / 'runtime.env').write_text('private-example')
                record['path'] = str(folder)
            history = {'trace-learn-staging': snapshot['projects']['trace-learn-staging']['releases'][3:5]}
            (state / 'healthy-releases.json').write_text(json.dumps(history))
            snapshot['images'] = [self.image('owned-old', '0')]
            with patch.object(retention, 'ROOT', root), patch.object(retention, 'snapshot', return_value=snapshot), patch.object(retention, 'docker') as docker:
                result = retention.run(apply=True)
            self.assertEqual(result['errors'], [])
            self.assertFalse((folders / '0').exists())
            self.assertTrue((folders / '3/runtime.env').exists())
            self.assertTrue((folders / '5/runtime.env').exists())
            docker.assert_called_once_with('image', 'rm', 'owned-old')

    def test_deployment_lock_prevents_snapshot_and_deletion(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for project in retention.PROJECTS: (root / project).mkdir()
            with (root / retention.PROJECTS[0] / 'deploy.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                with patch.object(retention, 'ROOT', root), patch.object(retention, 'snapshot') as snap:
                    result = retention.run(apply=True)
                self.assertIn('lock busy', result['skipped'])
                snap.assert_not_called()

    def test_failed_health_check_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            for project in retention.PROJECTS: (root / project).mkdir()
            with patch.object(retention, 'ROOT', root), patch.object(retention, 'snapshot', side_effect=RuntimeError('unhealthy')), patch.object(retention, 'docker') as docker:
                result = retention.run(apply=True)
            self.assertEqual(result['removedReleasePaths'], [])
            self.assertEqual(result['errors'], ['RuntimeError: unhealthy'])
            docker.assert_not_called()

    @staticmethod
    def image(identity, digest, extra=None):
        refs = [REPO + '@sha256:' + digest] if digest else []
        if extra: refs.append(extra)
        return {'id': identity, 'refs': refs, 'created': NOW - 200_000, 'size': 100}


if __name__ == '__main__':
    unittest.main()
