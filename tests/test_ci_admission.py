import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
SOURCE = Path(__file__).resolve().parents[1] / 'lib/ci-admission.py'
spec = importlib.util.spec_from_file_location('ci_admission', SOURCE)
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)

OWNER = 'Just-Understanding-Data-Ltd/'
PRODUCTS = [OWNER + 'trace-learn', OWNER + 'smart-cub-learning']
POLICIES = {
    'deploy-compose.yml': ['guard_ref', 'build_images_amd64', 'release_gate'],
    'lockfile-check.yml': ['lockfile-check'],
}


def environment(repository, workflow, job, ref='refs/heads/main'):
    return {'GITHUB_REPOSITORY': repository, 'GITHUB_WORKFLOW_REF': repository + '/.github/workflows/' + workflow + '@' + ref, 'GITHUB_JOB': job}


class ClassificationTests(unittest.TestCase):
    def test_original_and_renamed_product_keep_the_same_exact_control_jobs(self):
        for repository in PRODUCTS:
            for workflow, jobs in POLICIES.items():
                for job in jobs:
                    with self.subTest(repository=repository, workflow=workflow, job=job):
                        self.assertTrue(admission.is_control_job(environment(repository, workflow, job)))

    def test_other_deploy_and_lockfile_jobs_remain_heavy(self):
        for repository in PRODUCTS:
            for workflow in POLICIES:
                for job in ['deploy_primary', 'deploy_standby', 'migrate', 'unit', 'integration', 'browser', 'build', '', 'release_gate_extra']:
                    with self.subTest(repository=repository, workflow=workflow, job=job):
                        self.assertFalse(admission.is_control_job(environment(repository, workflow, job)))

    def test_job_names_cannot_cross_the_workflow_boundary(self):
        for repository in PRODUCTS:
            self.assertFalse(admission.is_control_job(environment(repository, 'deploy-compose.yml', 'lockfile-check')))
            self.assertFalse(admission.is_control_job(environment(repository, 'lockfile-check.yml', 'release_gate')))
            self.assertFalse(admission.is_control_job(environment(repository, 'unknown.yml', 'release_gate')))
            self.assertFalse(admission.is_control_job(environment(repository, 'deploy-compose.yml.bak', 'release_gate')))

    def test_octospark_policies_and_unknown_heavy_default_are_unchanged(self):
        repository = OWNER + 'octospark'
        for workflow in ['release-compose.yml', 'integration-tests.yml']:
            for job in ['guard_trusted_ref', 'build_images_amd64', 'release_gate']:
                self.assertTrue(admission.is_control_job(environment(repository, workflow, job)))
            self.assertFalse(admission.is_control_job(environment(repository, workflow, 'deploy_primary')))
        self.assertFalse(admission.is_control_job(environment(repository, 'lockfile-check.yml', 'lockfile-check')))
        for repository in ['SomeoneElse/smart-cub-learning', OWNER + 'smart-cub-learning-extra', OWNER + 'unknown', 'just-understanding-data-ltd/smart-cub-learning']:
            self.assertFalse(admission.is_control_job(environment(repository, 'deploy-compose.yml', 'release_gate')))

    def test_mismatched_or_missing_repository_workflow_metadata_stays_heavy(self):
        for repository in PRODUCTS:
            other = PRODUCTS[1] if repository == PRODUCTS[0] else PRODUCTS[0]
            data = environment(repository, 'deploy-compose.yml', 'release_gate')
            data['GITHUB_WORKFLOW_REF'] = other + '/.github/workflows/deploy-compose.yml@refs/heads/main'
            self.assertFalse(admission.is_control_job(data))
            data['GITHUB_WORKFLOW_REF'] = repository + '/not-workflows/deploy-compose.yml@refs/heads/main'
            self.assertFalse(admission.is_control_job(data))
            for missing in ['GITHUB_REPOSITORY', 'GITHUB_WORKFLOW_REF', 'GITHUB_JOB']:
                data = environment(repository, 'deploy-compose.yml', 'release_gate')
                data.pop(missing)
                self.assertFalse(admission.is_control_job(data))

    def test_new_alias_preserves_existing_ref_classification_without_new_branch_rules(self):
        for ref in ['refs/heads/main', 'refs/heads/staging', 'refs/pull/17/merge', 'refs/tags/v1']:
            for workflow, jobs in POLICIES.items():
                for job in jobs:
                    old = admission.is_control_job(environment(PRODUCTS[0], workflow, job, ref))
                    renamed = admission.is_control_job(environment(PRODUCTS[1], workflow, job, ref))
                    self.assertEqual(old, renamed)

    def test_recognised_new_control_jobs_preserve_heavy_leases(self):
        for workflow, jobs in POLICIES.items():
            for job in jobs:
                with patch.dict(admission.os.environ, environment(PRODUCTS[1], workflow, job), clear=True), patch.object(admission, 'owner', return_value=(123, 'owned-worker')) as owner, patch.object(admission, 'acquire', return_value=(Mock(), 0)) as acquire, patch.object(admission.os, 'fork', return_value=1) as fork, contextlib.redirect_stdout(io.StringIO()):
                    admission.main()
                owner.assert_called_once_with()
                acquire.assert_not_called()
                fork.assert_not_called()

    def test_unknown_jobs_still_acquire_one_of_two_slots(self):
        lease = Mock()
        with patch.dict(admission.os.environ, environment(PRODUCTS[1], 'deploy-compose.yml', 'deploy_primary'), clear=True), patch.object(admission, 'owner', return_value=(123, 'owned-worker')), patch.object(admission, 'acquire', return_value=(lease, 1)) as acquire, patch.object(admission.os, 'fork', return_value=1), contextlib.redirect_stdout(io.StringIO()) as output:
            admission.main()
        self.assertEqual(acquire.call_args.args[1:], (123, 'owned-worker'))
        lease.close.assert_called_once_with()
        self.assertIn('slot 2/2', output.getvalue())

    def test_worker_ancestry_is_required_even_for_control_jobs(self):
        with patch.dict(admission.os.environ, environment(PRODUCTS[1], 'deploy-compose.yml', 'release_gate'), clear=True), patch.object(admission, 'owner', side_effect=SystemExit('No worker')), patch.object(admission, 'acquire') as acquire:
            with self.assertRaises(SystemExit):
                admission.main()
        acquire.assert_not_called()


class HeavyLeaseTests(unittest.TestCase):
    def test_two_real_flocks_block_a_third_until_release_without_touching_host_state(self):
        with tempfile.TemporaryDirectory(prefix='ci-admission-test-') as directory, patch.object(admission, 'identity', return_value='owned-worker'):
            leases = []
            try:
                for expected_slot in [0, 1]:
                    with patch.object(admission.time, 'monotonic', side_effect=[0, 1]):
                        lease, slot = admission.acquire(Path(directory), 123, 'owned-worker', timeout=10)
                    leases.append(lease)
                    self.assertEqual(slot, expected_slot)
                with patch.object(admission.time, 'monotonic', side_effect=[0, 1, 11]), patch.object(admission.time, 'sleep') as sleep:
                    with self.assertRaisesRegex(SystemExit, 'timed out or the owning job ended'):
                        admission.acquire(Path(directory), 123, 'owned-worker', timeout=10)
                sleep.assert_called_once_with(1)
                self.assertEqual(sorted(path.name for path in Path(directory).iterdir()), ['slot-0.lock', 'slot-1.lock'])
                leases[0].close()
                with patch.object(admission.time, 'monotonic', side_effect=[0, 1]):
                    lease, slot = admission.acquire(Path(directory), 123, 'owned-worker', timeout=10)
                leases.append(lease)
                self.assertEqual(slot, 0)
            finally:
                for lease in leases:
                    lease.close()


if __name__ == '__main__':
    unittest.main()
