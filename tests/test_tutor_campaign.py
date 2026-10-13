"""Paid continuation must preserve deadlines, held requests and the shared cap."""
import datetime
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import PaidCache, atomic_json, provider_request, request_key
from configuration import campaign_window
from new_leads import classify_delta
import ssl


class CampaignWindowTests(unittest.TestCase):
    def test_certificate_repair_preserves_tls_verification(self):
        from discovery import tls_context
        context = tls_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_twelve_hour_window_is_anchored_to_the_request(self):
        manifest = {'hours': 12, 'authorizedStartedAt': '2026-10-13T11:43:51+00:00',
                    'authorizedDeadline': '2026-10-13T23:43:51+00:00'}
        start, deadline, cutoff = campaign_window(manifest)
        self.assertEqual(start.isoformat(), manifest['authorizedStartedAt'])
        self.assertEqual(deadline.isoformat(), manifest['authorizedDeadline'])
        self.assertEqual(deadline - cutoff, datetime.timedelta(minutes=15))
        for changed in [dict(manifest, authorizedDeadline='2026-10-14T00:43:51+00:00'),
                        {'hours': 12}]:
            with self.assertRaises(ValueError):
                campaign_window(changed)

    def test_legacy_job_retains_its_six_hour_window(self):
        start, deadline, cutoff = campaign_window({})
        self.assertEqual(deadline - start, datetime.timedelta(hours=6))
        self.assertEqual(cutoff - start, datetime.timedelta(hours=5, minutes=45))


class CampaignBudgetTests(unittest.TestCase):
    def test_receipt_reuse_still_rejects_a_changed_cached_file(self):
        from discovery import file_hash
        with tempfile.TemporaryDirectory() as temporary:
            cache = PaidCache(Path(temporary), 3065, 6935)
            payload = {'keyword': 'tutor Amersham'}
            response = {'cost': .01, 'status_code': 20000, 'tasks': [{'id': 'cached', 'cost': .01, 'status_code': 20000}]}
            cache.import_receipt('organic', payload, response)
            row = cache.get(request_key('organic', payload))
            self.assertEqual(cache.receipt(row), response)
            with patch('discovery.file_hash', wraps=file_hash) as hashing:
                self.assertEqual(cache.receipt(row), response)
                hashing.assert_not_called()
            Path(row['receipt']).write_text('{"response":{}}')
            with self.assertRaisesRegex(ValueError, 'receipt changed'):
                cache.receipt(row)
            cache.db.close()
    def test_imported_uncertainty_is_never_bought_again_or_charged_twice(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = PaidCache(Path(temporary), 3065, 6935)
            payload = {'keyword': 'tutor Amersham', 'depth': 30}
            cache.import_unsettled('organic', payload, 'Earlier unverified response')
            with patch('discovery.urllib.request.build_opener') as network:
                self.assertIsNone(provider_request(cache, 'organic', payload))
                network.assert_not_called()
            self.assertEqual(cache.usage()['remainingCents'], 3065)
            self.assertEqual(cache.usage()['sharedExposureCents'], 6935)
            self.assertEqual(cache.get(request_key('organic', payload))['status'], 'uncertain')
            self.assertEqual(cache.usage()['importedUnsettledRequests'], 1)
            cache.db.close()

    def test_archived_uncertainty_preserves_the_conservative_exposure(self):
        from prepare_campaign import archive_history
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary)
            root = history / 'tutor-lead-pathways-prior'
            cache = PaidCache(root, 5000, 1000)
            payload = {'keyword': 'tutor Aylesbury'}
            key = request_key('maps', payload)
            cache.reserve(key, 'maps', payload, 100)
            cache.mark_uncertain(key, 'No response saved')
            cache.db.close()
            atomic_json(root / 'runtime.json', {'phase': 'complete'})
            exposure, archives = archive_history(history)
            self.assertEqual(exposure, 1100)
            self.assertIsNone(archives[0][1][0][2])
            atomic_json(root / 'runtime.json', {'phase': 'running'})
            with self.assertRaisesRegex(ValueError, 'not terminal'):
                archive_history(history)


class CampaignNoveltyTests(unittest.TestCase):
    def test_known_profiles_are_excluded_without_inflating_the_new_audit(self):
        from audit_directory_identities import run as audit
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = 'https://www.findtutors.co.uk/tutor/jane.htm'
            atomic_json(root / 'bucks-private-tutors.json', [{'name': 'Jane', 'sourceUrls': [url]}])
            value = audit(root, root / 'review', None, exclude_source_urls={url})
            self.assertEqual(value['excludedKnownProfileRows'], 1)
            self.assertEqual(value['acceptedIdentities'], 0)
            self.assertEqual(value['pageEvidenceVerified'], 0)
            with self.assertRaises((KeyError, ValueError)):
                audit(root, root / 'full-review', None)

    def test_reclassifying_an_existing_page_does_not_count_as_a_new_contact(self):
        old = [{'canonicalId': 'page', 'name': 'Title before extraction',
                'sourceUrls': ['https://www.findtutors.co.uk/tutor/jane.htm'], 'outreachEligible': False}]
        current = [{'canonicalId': 'person', 'name': 'Jane Smith',
                    'sourceUrls': ['https://www.findtutors.co.uk/tutor/jane.htm/'], 'outreachEligible': False}]
        result = classify_delta(old, current)
        self.assertEqual(result['new'], [])
        self.assertEqual(len(result['known']), 1)

    def test_old_business_phone_prevents_a_register_alias_from_counting_as_new(self):
        old = [{'canonicalId': 'business:https://old-tutor.co.uk/:01494 123456',
                'name': 'Jane Smith Tutoring', 'sourceUrls': ['https://old-tutor.co.uk/']}]
        current = [{'canonicalId': 'register:jane', 'name': 'Jane Smith', 'phone': '+44 1494 123456',
                    'sourceUrls': ['https://register.co.uk/tutors'], 'outreachEligible': False}]
        result = classify_delta(old, current)
        self.assertEqual(result['new'], [])
        self.assertEqual(len(result['known']), 1)

    def test_own_organisation_matching_a_known_business_holds_people_without_merging_them(self):
        old = [{'canonicalId': 'business:known', 'name': 'Example Tuition', 'sourceUrls': []}]
        people = [{'canonicalId': key, 'name': name, 'organization': 'Example Tuition',
                   'sourceUrls': ['https://register.test/' + key], 'outreachEligible': False}
                  for key, name in [('jane', 'Jane Smith'), ('jordan', 'Jordan Brown')]]
        result = classify_delta(old, people)
        self.assertEqual(result['new'], [])
        self.assertEqual(result['known'], [])
        self.assertEqual([r['canonicalId'] for r in result['needsReview']], ['jane', 'jordan'])
        self.assertEqual({r['deltaHoldReason'] for r in result['needsReview']},
                         {'possible_baseline_organisation_match'})
        unrelated = dict(people[0], organization='Another Tuition Business')
        self.assertEqual(classify_delta(old, [unrelated])['new'], [unrelated])

    def test_sdk_suggestions_need_target_wording_and_an_approved_town(self):
        from run import sdk_query_supplements
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root / 'sdk/results/slot-00.json', {'nextQueries': [
                'site:tutorhunt.com "Amersham" "11+ tutor"', 'plumber Amersham', 'tutor Oxford',
                'https://localhost/tutor-Amersham', 'tutor Amersham\nnext command']})
            cache = PaidCache(root, 3065, 6935)
            job = {'towns': [{'name': 'Amersham', 'coordinate': '51.677,-0.607'}]}
            result = sdk_query_supplements(root, job, cache)
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]['town'], 'Amersham')
            self.assertIn('11%2B', result[0]['payload']['keyword'])
            cache.db.close()

    def test_engine_failure_gets_one_simpler_subject_query_with_a_distinct_cache_key(self):
        from run import directory_fallback
        payload = {'keyword': 'site:tutorhunt.com "Flackwell Heath" ("11 plus" OR "eleven plus" OR "entrance exam")'}
        item = {'lane': 'directories', 'payload': payload}
        error = {'status_code': 20000, 'tasks': [{'status_code': 40101}]}
        fallback = directory_fallback(item, error)
        self.assertEqual(fallback['keyword'], 'site:tutorhunt.com "Flackwell Heath" "11 plus"')
        self.assertNotEqual(request_key('directories', payload), request_key('directories', fallback))
        self.assertIsNone(directory_fallback(dict(item, payload=fallback), error))
        self.assertIsNone(directory_fallback(item, None))
        self.assertIsNone(directory_fallback(item, {'status_code': 20000, 'tasks': [{'status_code': 40102}]}))
        organic = {'lane': 'organic', 'payload': {'keyword': '"Amersham" tutor (inurl:newsletter OR filetype:pdf)'}}
        self.assertEqual(directory_fallback(organic, error)['keyword'], '"Amersham" tutor inurl:newsletter')

    def test_successful_receipt_over_the_reservation_stays_held(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = PaidCache(Path(temporary), 3065, 6935)
            payload = {'keyword': 'tutor High Wycombe'}
            key = request_key('organic', payload)
            self.assertTrue(cache.reserve(key, 'organic', payload, 100))
            with self.assertRaisesRegex(ValueError, 'reservation'):
                cache.settle(key, {'cost': 1.01, 'status_code': 20000,
                                   'tasks': [{'id': 'task', 'cost': 1.01, 'status_code': 20000}]})
            self.assertEqual(cache.get(key)['status'], 'submitting')
            self.assertEqual(cache.usage()['reservedCents'], 100)
            cache.db.close()


if __name__ == '__main__':
    unittest.main()
