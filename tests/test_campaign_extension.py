"""An extra window must not reset spend, uncertainty, tokens or collected evidence."""
import datetime
import fcntl
import json
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'
sys.path.insert(0, str(HERE))
from campaign_extension import validate_request, owners_sealed, extension_authorization, check_preparation, watch
from configuration import profile_page_ceiling
from discovery import PaidCache, atomic_json, file_hash, request_key
from prepare_campaign import prepare
from campaign_watch import aggregate_contacts, continue_classifier, terminal_cleanup


class ExtensionTests(unittest.TestCase):
    def test_new_campaign_monitor_can_run_before_its_owner_writes_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            self.assertFalse(terminal_cleanup(base / 'paid', base / 'profiles', {}))
    def fixture(self, base):
        base = base.resolve()
        parent, child = base / 'tutor-lead-pathways-parent', base / 'tutor-lead-pathways-extension'
        profile, focused = base / 'bucks-private-tutors-parent', base / 'tutor-focused-parent'
        cache = PaidCache(parent, 3065, 6935)
        payload = {'keyword': 'tutor Amersham'}
        cache.import_unsettled('organic', {'keyword': 'older tutor Amersham'}, 'older hold, already in prior floor')
        key = request_key('organic', payload)
        cache.reserve(key, 'organic', payload, 100)
        cache.mark_uncertain(key, 'current missing response')
        cache.db.close()
        atomic_json(parent / 'runtime.json', {'phase': 'complete'})
        atomic_json(parent / 'baseline/contacts.json', [])
        atomic_json(parent / 'exports/businesses.json', [])
        atomic_json(parent / 'exports/additional-contacts.json', [{'canonicalId': 'person:known',
            'name': 'Alice Smith', 'sourceUrls': ['https://www.findtutors.co.uk/tutor/alice.htm'],
            'outreachEligible': False}])
        atomic_json(parent / 'sdk/status.json', {'phase': 'completed', 'totalInputTokens': 3900000,
            'totalOutputTokens': 100000, 'completedTurns': 22, 'providerCompletedTurns': 22})
        atomic_json(parent / 'towns.json', [{'name': 'Amersham', 'coordinate': '51.677,-0.607'}])
        atomic_json(focused / 'profile-candidates-not-in-baseline.json', [])
        atomic_json(base / 'identities.json', [])
        (base / 'identities.csv').write_text('canonicalId,name\n')
        (base / 'crawler').write_text('test crawler')
        (base / 'codex').write_text('test codex')
        atomic_json(profile / 'job.json', {'startedAt': '2026-10-13T11:43:51+00:00',
            'deadlineUnix': 1791935031.0, 'pageLimit': 19983, 'target': 5000})
        atomic_json(profile / 'state.json', {'phase': 'stage_complete', 'attempts': 18000,
            'queue': [], 'seen': ['https://tutorextra.co.uk/tutors-in/amersham'], 'records': [], 'errors': []})
        atomic_json(profile / 'status.json', {'phase': 'stage_complete'})
        for name in ('pages', 'receipts', 'seed-batches'):
            (profile / name).mkdir()
        manifest = {'hours': 12, 'authorizedStartedAt': '2026-10-13T11:43:51+00:00',
            'authorizedDeadline': '2026-10-13T23:43:51+00:00', 'reservedDecisionsLifetimeCents': 300,
            'sharedCapCents': 10000, 'priorExposureCents': 6935, 'priorDfsExposureCents': 6635,
            'sdkTokenCeiling': 4000000, 'maximumSdkTurns': 24, 'profileRunDir': str(profile),
            'focusedParent': str(focused), 'focusedContinuation': str(focused), 'focusedRoots': [str(focused)],
            'identityBaselineCsv': str(base / 'identities.csv'), 'codexBinary': str(base / 'codex'),
            'crawlerBinary': str(base / 'crawler')}
        atomic_json(parent / 'manifest.json', manifest)
        request = {'runId': child.name, 'profileRunId': 'bucks-private-tutors-extension',
            'parentCampaign': str(parent), 'parentManifestSha256': file_hash(parent / 'manifest.json'),
            'requestedAt': manifest['authorizedDeadline'], 'deadline': '2026-10-14T11:43:51+00:00',
            'userRequestedAt': '2026-10-13T18:58:00+00:00', 'campaignBudgetCapUsd': 100,
            'reservedDecisionsLifetimeCents': 300, 'sdkTokenCeiling': 4000000,
            'researchPriority': '11_plus_and_private_academic', 'newPublicPageAllowance': 12000}
        atomic_json(child / 'extension-request.json', request)
        return parent, child, profile, focused, request

    def test_same_lifetime_budget_preserves_holds_and_reserves_classifier_once(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            parent, child, _, _, request = self.fixture(base)
            auth = extension_authorization(request, parent, base, file_hash(child / 'extension-request.json'))
            self.assertEqual(auth['priorDfsExposureCents'], 6735)
            self.assertEqual(auth['maximumNewDfsCents'], 2965)
            self.assertEqual(auth['priorDfsExposureCents'] + 300 + auth['maximumNewDfsCents'], 10000)
            atomic_json(parent / 'runtime.json', {'phase': 'running'})
            with self.assertRaisesRegex(ValueError, 'not terminal'):
                extension_authorization(request, parent, base, 'hash')

    def test_exhausted_paid_allowance_allows_cache_reuse_without_new_paid_requests(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            parent, child, _, _, request = self.fixture(base)
            with patch('campaign_extension.archive_history', return_value=(9950, [])):
                auth = extension_authorization(request, parent, base, 'hash')
            self.assertEqual(auth['maximumNewDfsCents'], 0)
            self.assertEqual(auth['unallocatedBelowReservationCents'], 50)
            cache = PaidCache(child, 0, 9950)
            cache.import_unsettled('organic', {'keyword': 'tutor Amersham'}, 'prior hold')
            self.assertEqual(cache.usage()['remainingCents'], 0)
            self.assertFalse(cache.reserve(request_key('organic', {'keyword': 'new tutor'}),
                'organic', {'keyword': 'new tutor'}, 100))
            cache.db.close()

    def test_extra_window_cannot_reanchor_scope_cap_or_parent(self):
        with tempfile.TemporaryDirectory() as temp:
            parent, child, _, _, request = self.fixture(Path(temp))
            validate_request(request, parent, child)
            for changed in [dict(request, deadline='2026-10-14T12:43:51+00:00'),
                    dict(request, campaignBudgetCapUsd=200), dict(request, sdkTokenCeiling=8000000),
                    dict(request, parentManifestSha256='changed'), dict(request, researchPriority='all_UK'),
                    dict(request, newPublicPageAllowance=24000)]:
                with self.assertRaises(ValueError):
                    validate_request(changed, parent, child)

    def test_waiting_does_no_preparation_or_installation_and_keeps_original_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            parent, child, _, _, _ = self.fixture(Path(temp))
            before = (parent / 'manifest.json').read_bytes()
            with patch('campaign_extension.prepare') as preparing, patch('campaign_extension.install_registered') as installing:
                result = watch(child, file_hash(child / 'extension-request.json'), 'label', clock=lambda: 0)
            self.assertEqual(result['phase'], 'waiting_for_original_deadline')
            preparing.assert_not_called()
            installing.assert_not_called()
            self.assertFalse((child / 'paid.sqlite').exists())
            self.assertEqual(before, (parent / 'manifest.json').read_bytes())

    def test_active_parent_lock_holds_activation_even_with_terminal_status(self):
        with tempfile.TemporaryDirectory() as temp:
            parent, _, profile, _, _ = self.fixture(Path(temp))
            self.assertTrue(owners_sealed(parent, profile))
            with (profile / 'owner.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertFalse(owners_sealed(parent, profile))

    def test_actual_preparation_inherits_tokens_profiles_baseline_and_paid_cache(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            parent, child, profile, focused, request = self.fixture(base)
            auth = extension_authorization(request, parent, base, file_hash(child / 'extension-request.json'))
            atomic_json(child / 'authorization.json', auth)
            (base / '.codex').mkdir()
            (base / '.codex/config.toml').write_text('model="test"\n')
            parent_before = (parent / 'manifest.json').read_bytes(), (profile / 'state.json').read_bytes()
            with patch('prepare_campaign.Path.home', return_value=base):
                prepare(child, child / 'authorization.json', base, profile,
                    base / request['profileRunId'], base / 'identities.json', focused,
                    parent / 'towns.json', base / 'codex', base / 'crawler', extension_parent=parent)
            manifest = check_preparation(child, file_hash(child / 'extension-request.json'))
            self.assertEqual(manifest['priorExposureCents'], 7035)
            self.assertEqual(manifest['budgetCents'], 2965)
            self.assertEqual(continue_classifier(child, manifest), 'disabled_shared_allowance_exhausted')
            sdk = json.loads((child / 'sdk/status.json').read_text())
            self.assertEqual(sdk['totalInputTokens'] + sdk['totalOutputTokens'], 4000000)
            cache = PaidCache(child, 2965, 7035)
            self.assertEqual(cache.usage()['importedUnsettledRequests'], 2)
            self.assertEqual(cache.usage()['sharedExposureCents'], 7035)
            self.assertEqual(cache.usage()['remainingCents'], 2965)
            cache.db.close()
            job = json.loads((base / request['profileRunId'] / 'job.json').read_text())
            self.assertEqual(job['initialAttemptCount'], 18000)
            self.assertEqual(job['pageLimit'], 30000)
            self.assertEqual(profile_page_ceiling(job), 32000)
            self.assertEqual(parent_before, ((parent / 'manifest.json').read_bytes(), (profile / 'state.json').read_bytes()))
            job['deadlineUnix'] += 1
            with self.assertRaises(ValueError):
                profile_page_ceiling(job)
            atomic_json(child / 'plan.json', [])
            with self.assertRaisesRegex(ValueError, 'changed'):
                check_preparation(child, file_hash(child / 'extension-request.json'))

    def test_inherited_public_contact_is_not_lost_or_counted_twice(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            parent, child, _, _, _ = self.fixture(base)
            previous = parent / 'exports/additional-contacts.json'
            atomic_json(child / 'manifest.json', {'previousCampaignRoot': str(parent),
                'previousAdditionalContactsSha256': file_hash(previous)})
            atomic_json(child / 'baseline/contacts.json', [])
            output = base / 'audit'
            atomic_json(output / 'this-stage-delta/new.json', json.loads(previous.read_text()))
            aggregate_contacts(child, {'outputDirectory': str(output)})
            self.assertEqual(len(json.loads((child / 'exports/additional-contacts.json').read_text())), 1)
            atomic_json(output / 'this-stage-delta/new.json', [])
            aggregate_contacts(child, {'outputDirectory': str(output)})
            self.assertEqual(len(json.loads((child / 'exports/additional-contacts.json').read_text())), 1)
            previous.write_text('[]')
            with self.assertRaisesRegex(ValueError, 'changed'):
                aggregate_contacts(child, {'outputDirectory': str(output)})

    def test_no_extension_page_limit_without_bound_authorization(self):
        self.assertEqual(profile_page_ceiling({'pageLimit': 30000}), 20000)
        with tempfile.TemporaryDirectory() as temp:
            _, child, _, _, request = self.fixture(Path(temp))
            path = child / 'authorization.json'
            atomic_json(path, request)
            job = {'additionalWindowAuthorization': str(path),
                'additionalWindowAuthorizationSha256': 'wrong'}
            with self.assertRaisesRegex(ValueError, 'changed'):
                profile_page_ceiling(job)

    def test_activation_retries_installation_without_repreparing_or_resetting_spend(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp).resolve()
            parent, child, _, _, request = self.fixture(base)
            (base / '.codex').mkdir()
            (base / '.codex/config.toml').write_text('model="test"\n')
            observed = datetime.datetime.fromisoformat(request['requestedAt']).timestamp() + 60
            request_sha = file_hash(child / 'extension-request.json')
            real_run = subprocess.run
            def native_calls(command, **kwargs):
                if command[0] == 'launchctl':
                    return subprocess.CompletedProcess(command, 0, '', '')
                return real_run(command, **kwargs)
            with patch('prepare_campaign.Path.home', return_value=base), patch('campaign_extension.audit_parent'), \
                    patch('campaign_extension.subprocess.run', side_effect=native_calls), patch('discovery.urllib.request.build_opener') as network:
                with self.assertRaises(RuntimeError):
                    watch(child, request_sha, 'label', clock=lambda: observed,
                        installer=lambda *_: (_ for _ in ()).throw(RuntimeError('installer interrupted')))
                original = file_hash(child / 'paid.sqlite'), file_hash(child / 'manifest.json')
                with patch('campaign_extension.prepare') as preparing:
                    result = watch(child, request_sha, 'label', clock=lambda: observed,
                        installer=lambda *_: ['registered-task'])
                    preparing.assert_not_called()
                network.assert_not_called()
            self.assertEqual(result['phase'], 'activated')
            self.assertEqual(result['newDataForSeoCeilingCents'], 2965)
            self.assertEqual(original, (file_hash(child / 'paid.sqlite'), file_hash(child / 'manifest.json')))

    def test_expired_extension_never_prepares_and_edited_request_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            _, child, _, _, request = self.fixture(Path(temp))
            stamp = datetime.datetime.fromisoformat(request['deadline']).timestamp()
            sha = file_hash(child / 'extension-request.json')
            with patch('campaign_extension.subprocess.run'), patch('campaign_extension.prepare') as preparing:
                result = watch(child, sha, 'label', clock=lambda: stamp)
            self.assertEqual(result['phase'], 'extension_deadline_reached')
            preparing.assert_not_called()
            atomic_json(child / 'extension-request.json', dict(request, campaignBudgetCapUsd=200))
            with self.assertRaisesRegex(ValueError, 'Pinned'):
                watch(child, sha, 'label', clock=lambda: stamp)


if __name__ == '__main__':
    unittest.main()
