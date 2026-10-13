import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('ci_admission', Path(__file__).with_name('ci-admission.py'))
admission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(admission)


class ControlAdmissionTests(unittest.TestCase):
    def environment(self, repo, workflow, job):
        repository = 'Just-Understanding-Data-Ltd/' + repo
        return dict(GITHUB_REPOSITORY=repository, GITHUB_WORKFLOW_REF=repository + '/.github/workflows/' + workflow + '@refs/heads/staging', GITHUB_JOB=job)

    def test_known_control_steps_do_not_take_heavy_leases(self):
        for repo, workflow, jobs in [
            ('octospark', 'release-compose.yml', ['guard_trusted_ref', 'build_images_amd64', 'release_gate']),
            ('octospark', 'integration-tests.yml', ['guard_trusted_ref', 'build_images_amd64', 'release_gate']),
            ('trace-learn', 'deploy-compose.yml', ['guard_ref', 'build_images_amd64', 'release_gate']),
            ('trace-learn', 'lockfile-check.yml', ['lockfile-check']),
        ]:
            for job in jobs:
                with self.subTest(repo=repo, workflow=workflow, job=job):
                    self.assertTrue(admission.is_control_job(self.environment(repo, workflow, job)))

    def test_builds_tests_deployments_and_unknown_jobs_keep_heavy_leases(self):
        for job in ['build_images_locally', 'integration', 'e2e', 'deploy', 'quality', 'deploy_compose_staging']:
            self.assertFalse(admission.is_control_job(self.environment('octospark', 'integration-tests.yml', job)))

    def test_unknown_contexts_fail_closed(self):
        self.assertFalse(admission.is_control_job({}))
        self.assertFalse(admission.is_control_job(self.environment('another-project', 'release-compose.yml', 'release_gate')))
        self.assertFalse(admission.is_control_job(self.environment('trace-learn', 'another-workflow.yml', 'lockfile-check')))
        env = self.environment('trace-learn', 'lockfile-check.yml', 'lockfile-check')
        env['GITHUB_WORKFLOW_REF'] = 'another-repo/.github/workflows/lockfile-check.yml@refs/heads/main'
        self.assertFalse(admission.is_control_job(env))


if __name__ == '__main__':
    unittest.main()
