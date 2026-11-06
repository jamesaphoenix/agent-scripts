import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'agent-loops/docker-cleanup/cleanup-hetzner-images.py'
spec = importlib.util.spec_from_file_location('hetzner_cleanup', SCRIPT)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)

REPO = 'europe-west3-docker.pkg.dev/octospark-prod/tx-agent-kit/renderer'
def image(n, repo=REPO):
    return {'Id': 'sha256:' + str(n) * 64, 'RepoTags': [],
            'RepoDigests': [repo + '@sha256:' + str(n) * 64],
            'Created': '2020-01-01T00:00:00Z',
            'Metadata': {'LastTagTime': '2020-01-01T00:00:00Z'}}

class HetznerImageTests(unittest.TestCase):
    def test_container_native_and_rollback_aliases_are_protected(self):
        images = [image(n) for n in range(1, 6)]
        refs = {images[1]['RepoDigests'][0], images[2]['RepoDigests'][0]}
        containers = [{'Image': images[0]['Id']}]  # includes stopped / restore jobs
        self.assertEqual([x['Id'] for x in cleanup.candidates(images, containers, refs)],
                         [images[3]['Id'], images[4]['Id']])

    def test_unknown_repositories_shared_aliases_and_dangling_are_retained(self):
        images = [image(1), image(2, 'jud-restore/postgres'), image(3)]
        images[0]['RepoTags'] = ['another-app:rollback']
        images[2]['RepoDigests'] = []
        self.assertEqual(cleanup.candidates(images, [], set()), [])

    def test_fresh_or_missing_local_tag_time_is_retained(self):
        images = [image(1), image(2)]
        images[0]['Metadata']['LastTagTime'] = '2999-01-01T00:00:00Z'
        images[1].pop('Metadata')
        self.assertEqual(cleanup.candidates(images, [], set()), [])

    def test_two_distinct_rollbacks_per_environment_not_global_latest(self):
        def record(n): return {'images': {'api': str(n)}, 'sourceSha': str(n)}
        history = {p: [record(1), record(2), record(3)] for p in cleanup.PROJECTS}
        current = {p: record(3) for p in cleanup.PROJECTS}
        result = cleanup.retain_history(history, current)
        self.assertEqual([r['sourceSha'] for r in result['octospark-staging']], ['1','2','3'])
        self.assertEqual([r['sourceSha'] for r in result['octospark-prod']], ['1','2','3'])

    def test_multiple_unseen_deployments_require_audited_history_refresh(self):
        def record(n): return {'images': {'api': str(n)}, 'sourceSha': str(n)}
        history = {p: [record(1), record(2), record(3)] for p in cleanup.PROJECTS}
        current = {p: record(3) for p in cleanup.PROJECTS}
        # Releases 4 and 5 happened between daily runs. Appending 5 would
        # silently discard the actual most recent rollback, release 4.
        current['octospark-staging'] = record(5)
        with self.assertRaisesRegex(ValueError, 'audited success history'):
            cleanup.retain_history(history, current)

    def test_duplicate_image_bundles_cannot_fill_rollback_slots(self):
        record = {'images': {'api': 'same'}, 'sourceSha': 'a'}
        with self.assertRaises(ValueError):
            cleanup.retain_history({p: [record, record, record] for p in cleanup.PROJECTS},
                                   {p: record for p in cleanup.PROJECTS})

    def test_snapshot_drift_aborts_before_any_deletion(self):
        plan = {'fingerprint': 'original', 'candidates': [image(1)]}
        with patch.object(cleanup, 'snapshot', return_value={'fingerprint':'changed'}), \
             patch.object(cleanup, 'docker') as docker:
            with self.assertRaises(ValueError): cleanup.apply(plan, Path('/unused'))
            docker.assert_not_called()

    def test_rechecks_before_each_image_and_never_forces_or_prunes(self):
        plan = {'fingerprint':'same', 'candidates':[image(1), image(2)]}
        snapshot = dict(plan, protected=set(), containers=[])
        with patch.object(cleanup, 'snapshot', side_effect=[snapshot, {'fingerprint':'changed'}]), \
             patch.object(cleanup, 'docker', return_value=json.dumps([image(1)])) as docker:
            with self.assertRaises(ValueError): cleanup.apply(plan, Path('/unused'))
            self.assertEqual(docker.call_args_list[1].args, ('image','rm',image(1)['Id']))
            self.assertEqual(len(docker.call_args_list), 2)

    def test_inventory_failure_is_fatal(self):
        with patch.object(cleanup, 'docker', side_effect=RuntimeError('unavailable')):
            with self.assertRaises(RuntimeError): cleanup.inspect_all('container')

    def test_malformed_or_changed_receipt_is_fatal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'images.env'; path.write_text('SOURCE_SHA=' + 'a'*40 + '\nAPI_IMAGE=unsafe:latest\n')
            with self.assertRaises(ValueError): cleanup.read_artifact(path, 'octospark')
            with self.assertRaises(ValueError): cleanup.validate_record({'artifact':str(path)}, 'octospark')


class HetznerInstallerTests(unittest.TestCase):
    def module(self):
        spec=importlib.util.spec_from_file_location('hetzner_installer', SCRIPT.with_name('install-hetzner-cron.py'))
        module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module

    def test_preserves_unrelated_jobs_and_replaces_only_managed_line(self):
        installer=self.module()
        existing='MAILTO=ops@example.test\n0 1 * * * /backup\n30 3 * * * old-cleaner ' + installer.MARKER + '\n'
        output=installer.crontab_text(existing)
        self.assertIn('MAILTO=ops@example.test\n0 1 * * * /backup\n', output)
        self.assertEqual(output.count(installer.MARKER),1)
        self.assertNotIn('old-cleaner',output)
        self.assertIn('30 3 * * * /usr/bin/python3', output)
        self.assertIn('cleanup-hetzner-images.py --cron',output)

    def test_refuses_ambiguous_duplicate_cron_entries(self):
        installer=self.module()
        with self.assertRaises(ValueError): installer.crontab_text((installer.MARKER+'\n')*2)

class NativeHistoryTests(unittest.TestCase):
    def test_native_deploy_without_matching_audited_bundle_refuses_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            proof={'environment':'staging','canaryPassed':True,'sourceSha':'a'*40,
                   'rendererImage':REPO+'@sha256:'+'4'*64,
                   'mediaImage':REPO.replace('renderer','media-runtime')+'@sha256:'+'3'*64}
            (root/'release-staging.json').write_text(json.dumps(proof))
            current={'octospark-staging':{'images':{'RENDERER_IMAGE_AMD64':REPO+'@sha256:'+'2'*64}}}
            with patch.object(cleanup,'NATIVE',root):
                with self.assertRaisesRegex(ValueError,'audited success history'):
                    cleanup.native_pins(current)


class RetiredProjectTests(unittest.TestCase):
    def test_explicit_retirement_preserves_history_without_requiring_service(self):
        history={p:[{'images':{'api':p}}] for p in cleanup.PROJECTS}
        retired=['octospark-staging','trace-learn-staging']
        with patch.object(cleanup,'current_release',return_value={'images':{'api':'active'}}) as current:
            result=cleanup.current_states(history,[],retired)
        self.assertEqual({call.args[0] for call in current.call_args_list}, {'octospark-prod','trace-learn-prod'})
        self.assertEqual(result['octospark-staging'],history['octospark-staging'][-1])
        self.assertEqual(result['trace-learn-staging'],history['trace-learn-staging'][-1])

    def test_unknown_or_duplicate_retirement_is_rejected(self):
        for retired in [['other-prod'],['octospark-prod','octospark-prod'],'octospark-prod']:
            with self.subTest(retired=retired),self.assertRaises(ValueError):
                cleanup.retired_projects({'retiredProjects':retired})

    def test_missing_active_service_still_refuses_cleanup(self):
        with self.assertRaises(ValueError): cleanup.current_release('trace-learn-staging',[])

if __name__ == '__main__': unittest.main()
