import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys


MODULE = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways/discovery.py"
spec = importlib.util.spec_from_file_location("tutor_pathways", MODULE)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)


class SecretLaunchTests(unittest.TestCase):
    def load_launcher(self):
        path = MODULE.with_name("launch.py")
        module_spec = importlib.util.spec_from_file_location("tutor_launch", path)
        launcher = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(launcher)
        return launcher

    def test_only_provider_credentials_reach_the_runner(self):
        launcher = self.load_launcher()
        environment = {"PATH": "/bin", "HOME": "/user", "OP_SERVICE_ACCOUNT_TOKEN": "test-token",
                       "GEMINI_API_KEY": "unrelated", "OPENAI_API_KEY": "unrelated"}
        replies = [subprocess.CompletedProcess([], 0, "test-login\n", ""),
                   subprocess.CompletedProcess([], 0, "test-password\n", "")]
        with patch.object(launcher.subprocess, "run", side_effect=replies) as read:
            result = launcher.runner_environment(environment)
        self.assertEqual(result, {"PATH": "/bin", "HOME": "/user", "DATAFORSEO_LOGIN": "test-login", "DATAFORSEO_PASSWORD": "test-password"})
        self.assertEqual(read.call_count, 2)
        self.assertEqual(read.call_args.kwargs["timeout"], 50)
        self.assertIn('run_op read "$2"', read.call_args.args[0][2])
        self.assertNotIn("GEMINI_API_KEY", read.call_args.kwargs["env"])

    def test_auth_failure_stops_without_disclosing_secret_output(self):
        launcher = self.load_launcher()
        with patch.object(launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, "sensitive-output", "auth error")):
            with self.assertRaisesRegex(RuntimeError, "1Password credential read failed") as error:
                launcher.runner_environment({"OP_SERVICE_ACCOUNT_TOKEN": "test-token"})
            self.assertNotIn("sensitive-output", str(error.exception))


class PaidCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.cache = discovery.PaidCache(Path(self.directory.name), 200, 9800)

    def tearDown(self):
        self.cache.db.close()
        self.directory.cleanup()

    def test_request_identity_ignores_tracking_tag_but_keeps_depth(self):
        first = discovery.request_key("maps", {"keyword": "11 plus tutor", "depth": 100, "tag": "old"})
        self.assertEqual(first, discovery.request_key("maps", {"depth": 100, "keyword": "11 plus tutor", "tag": "new"}))
        self.assertNotEqual(first, discovery.request_key("maps", {"keyword": "11 plus tutor", "depth": 10}))

    def test_settled_and_uncertain_requests_cannot_be_bought_again(self):
        self.assertTrue(self.cache.reserve("one", "maps", {"keyword": "one"}, 100))
        self.cache.settle("one", {"status_code": 20000, "cost": 0.002, "tasks": [{"id": "task-one", "cost": 0.002, "status_code": 20000}]})
        self.assertFalse(self.cache.reserve("one", "maps", {"keyword": "one"}, 100))
        self.assertTrue(self.cache.reserve("two", "maps", {"keyword": "two"}, 100))
        self.cache.mark_uncertain("two", "connection lost")
        self.assertFalse(self.cache.reserve("two", "maps", {"keyword": "two"}, 100))
        self.assertEqual(self.cache.usage()["uncertainCents"], 100)

    def test_parallel_reservations_share_one_cap(self):
        other = discovery.PaidCache(Path(self.directory.name), 200, 9800)
        self.addCleanup(other.db.close)
        self.assertTrue(self.cache.reserve("one", "maps", {}, 150))
        self.assertFalse(other.reserve("two", "business", {}, 100))
        self.assertEqual(other.usage()["exposureCents"], 150)

    def test_missing_or_mismatched_cost_stays_uncertain(self):
        self.cache.reserve("one", "maps", {}, 100)
        with self.assertRaises(ValueError):
            self.cache.settle("one", {"cost": 0.001, "tasks": [{"id": "one", "cost": 0.02}]})
        self.assertEqual(self.cache.usage()["reservedCents"], 100)

    def test_recorded_provider_failure_is_settled_and_never_retried(self):
        self.cache.reserve("one", "organic", {}, 100)
        self.cache.settle("one", {"status_code": 20000, "cost": 0.002, "tasks": [{"id": "task-one", "cost": 0.002, "status_code": 40101}]})
        self.assertEqual(self.cache.usage()["failed"], 1)
        self.assertFalse(self.cache.reserve("one", "organic", {}, 100))

    def test_crash_recovery_quarantines_submitting_calls(self):
        self.cache.reserve("one", "maps", {}, 100)
        self.cache.recover_interrupted()
        self.assertEqual(self.cache.usage()["uncertainCents"], 100)
        self.assertFalse(self.cache.reserve("one", "maps", {}, 100))

    def test_existing_run_budget_cannot_be_changed(self):
        with self.assertRaises(ValueError):
            discovery.PaidCache(Path(self.directory.name), 300, 9700)


class LeadCleaningTests(unittest.TestCase):
    def test_email_cleaner_drops_vendor_and_known_concatenation_artefacts(self):
        self.assertEqual(discovery.clean_emails(["info@mrsptutoring.co.uk", "emailinfo@mrsptutoring.co.uk", "084380contact@cleverclogsacademy.com", "contact@cleverclogsacademy.com", "support@learnworlds.com"]),
                         ["contact@cleverclogsacademy.com", "info@mrsptutoring.co.uk"])

    def test_natalie_frith_template_address_does_not_replace_her_business_contact(self):
        self.assertEqual(discovery.clean_emails(["info@edublink.com", "natfrith@icloud.com"]), ["natfrith@icloud.com"])

    def test_maps_business_survives_without_a_website(self):
        record = discovery.business_record({"title": "Primary Target Tutoring", "place_id": "place-one", "address": "High Wycombe HP13 6UJ", "phone": "+447780000000", "category": "Tutor"}, {"lane": "maps", "town": "High Wycombe"})
        self.assertIsNone(record["website"])
        self.assertEqual(record["placeId"], "place-one")
        self.assertFalse(record["localVerified"])
        self.assertFalse(record["outreachEligible"])

    def test_search_origin_never_becomes_a_verified_business_location(self):
        record = discovery.business_record({"title": "Tutor", "place_id": "place-one"}, {"lane": "maps", "town": "Penn"})
        self.assertIsNone(record["advertisedAddress"])
        self.assertFalse(record["localVerified"])

    def test_provider_email_is_preserved_as_a_claim_without_promoting_page_evidence(self):
        record = discovery.business_record({"title": "Local Tutor", "cid": "123", "contact_info": [
            {"type": "mail", "value": "advertised@tuition.example", "source": "backlinks"},
            {"type": "mail", "value": "info@mysite.com", "source": "backlinks"},
            {"type": "telephone", "value": "07400000000", "source": "google_business"}]}, {"lane": "business"})
        self.assertEqual(record["emails"], [])
        self.assertEqual(record["contactClaims"][0]["value"], "advertised@tuition.example")
        self.assertFalse(record["contactClaims"][0]["verified"])
        self.assertEqual(record["phone"], "07400000000")
        self.assertEqual(len(record["contactClaims"]), 2)

    def test_own_business_evidence_removes_the_source_only_flag(self):
        records = [{"name": "Local Tutor", "website": "https://local-tutor.example", "sourceOnly": True},
                   {"name": "Local Tutor", "website": "https://local-tutor.example", "placeId": "local-place", "category": "Tutor"}]
        merged = discovery.merge_businesses(records)
        self.assertFalse(merged[0]["sourceOnly"])

    def test_platform_tenants_are_not_merged_by_shared_host(self):
        one = {"name": "One", "website": "https://one.learnworlds.com", "provenance": []}
        two = {"name": "Two", "website": "https://two.learnworlds.com", "provenance": []}
        self.assertEqual(len(discovery.merge_businesses([one, two])), 2)

    def test_independent_profiles_on_the_same_directory_remain_separate(self):
        rows = [{"name": "Wendy", "website": "https://www.childcare.co.uk/profile/3678407"},
                {"name": "Harrietl", "website": "https://www.childcare.co.uk/profile/2240893"}]
        self.assertEqual(len(discovery.merge_businesses(rows)), 2)
        self.assertIsNone(discovery.hold_reason(rows[0]))
        self.assertEqual(discovery.hold_reason({"name": "Directory", "website": "https://www.childcare.co.uk/search/Private-Tutors/Bourne+End/11-Plus"}), "source_directory_or_job_page")

    def test_bark_profiles_remain_distinct_and_held_for_review(self):
        rows = [{"name": "Zofshan", "website": "https://www.bark.com/en/gb/company/private-tuition/a41d1/"},
                {"name": "Actualize Tutoring", "website": "https://www.bark.com/en/gb/company/actualize-tutoring/lpbYY/"},
                {"name": "A+ Tutoring", "website": "https://www.bark.com/en/gb/company/a-tutoring/ld3YG/"}]
        self.assertEqual(len(discovery.merge_businesses(rows)), 3)
        for row in rows:
            self.assertEqual(discovery.hold_reason(row), "source_directory_or_job_page")

    def test_directory_host_families_keep_distinct_paths_and_hold_generic_sources(self):
        rows = [{"name": "Tops Tuition", "website": "https://beaconsfield.cylex-uk.co.uk/company/tops-tuition-27065012.html"},
                {"name": "Another listing", "website": "https://beaconsfield.cylex-uk.co.uk/company/another-listing-12345678.html"}]
        self.assertEqual(len(discovery.merge_businesses(rows)), 2)
        for row in rows + [{"name": "Directory", "website": "https://www.yell.com/s/tutoring-chalfont+st+peter.html"},
                           {"name": "Directory", "website": "https://www.schoolguide.co.uk/tutordirectory/regional?search=farnham-common"},
                           {"name": "Directory", "website": "https://m.yelp.com/search?cflt=tutoring"}]:
            with self.subTest(website=row["website"]):
                self.assertEqual(discovery.hold_reason(row), "source_directory_or_job_page")
        self.assertEqual(discovery.website_key("https://my-independent-tutor.example/about"), "my-independent-tutor.example")

    def test_phone_merges_branches_and_preserves_all_places(self):
        rows = [{"name": "Tuition", "phone": "+447400000000", "placeId": "a", "provenance": ["a"]}, {"name": "Tuition", "phone": "07400 000000", "placeId": "b", "provenance": ["b"]}]
        merged = discovery.merge_businesses(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0]["placeIds"]), {"a", "b"})
        self.assertEqual(len(merged[0]["provenance"]), 2)

    def test_directory_jobs_and_chains_are_held(self):
        for row in [{"name": "Kumon Maths", "website": "https://kumon.co.uk/penn"}, {"name": "Tutor jobs", "website": "https://www.teachingpersonnel.com/job/tutor"}, {"name": "Tutor directory", "website": "https://www.11plusguide.com/tutors"}]:
            self.assertIsNotNone(discovery.hold_reason(row))

    def test_franchise_brands_are_held_by_name_and_domain(self):
        for row in [{"name": "Kip McGrath Aylesbury Tuition Centre"},
                    {"name": "Local tuition", "website": "https://www.kipmcgrath.co.uk/milton-keynes-north"},
                    {"name": "NumberWorks’nWords"},
                    {"name": "Local tuition", "website": "https://numberworksnwords.com/uk/our-locations"}]:
            with self.subTest(row=row):
                self.assertEqual(discovery.hold_reason(row), "recognised_chain")

    def test_non_academic_maps_categories_do_not_pad_tutor_counts(self):
        for name, category in [("Cowper and Newton Museum", "Museum"),
                               ("Rapid Book Test", "Driving school"),
                               ("Tai Chi Globe", "Martial arts school"),
                               ("Better Piano Lessons", "Piano instructor"),
                               ("ArtLife Studio", "Art school"),
                               ("Leadership Training", "Life coach"),
                               ("UK MI Translation Services", "Translation service")]:
            with self.subTest(category=category):
                self.assertEqual(discovery.hold_reason({"name": name, "category": category}), "outside_academic_tuition_scope")
        for row in [{"name": "Marie Hayes Primary Maths Tuition", "category": "Educational institution"},
                    {"name": "Primary School Tutor in Beaconsfield", "category": "Tutor"},
                    {"name": "Tutoring with Natalie", "category": "Educational consultant"}]:
            self.assertIsNone(discovery.hold_reason(row))

    def test_changing_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_text("[]")
            job = {"planSha256": discovery.file_hash(root / "plan.json")}
            discovery.validate_plan(root, job)
            (root / "plan.json").write_text("[1]")
            with self.assertRaises(ValueError):
                discovery.validate_plan(root, job)


class CountyCleaningTests(unittest.TestCase):
    def setUp(self):
        module_spec = importlib.util.spec_from_file_location("county_clean", MODULE.with_name("clean.py"))
        self.clean = importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules, {"discovery": discovery}):
            module_spec.loader.exec_module(self.clean)

    def test_hp_prefix_alone_does_not_establish_buckinghamshire(self):
        geography = {"HP108BP": {"result": {"admin_district": "Buckinghamshire", "codes": {"admin_district": "E06000060"}}},
                     "HP11AA": {"result": {"admin_district": "Dacorum", "codes": {"admin_district": "E07000096"}}}}
        self.assertEqual(self.clean.county_evidence({"advertisedAddress": "Penn HP10 8BP"}, geography)["status"], "has_bucks_listing")
        self.assertEqual(self.clean.county_evidence({"advertisedAddress": "Hemel Hempstead HP1 1AA"}, geography)["status"], "outside_recorded_listings")

    def test_a_bucks_branch_survives_a_shared_berkshire_website_identity(self):
        geography = {"SL11AA": {"result": {"codes": {"admin_district": "E06000039"}}},
                     "MK11AA": {"result": {"codes": {"admin_district": "E06000042"}}}}
        record = {"advertisedAddress": "Slough SL1 1AA", "establishments": [{"advertisedAddress": "Milton Keynes MK1 1AA"}]}
        evidence = self.clean.county_evidence(record, geography)
        self.assertEqual(evidence["status"], "has_bucks_listing")
        self.assertEqual(evidence["localAddresses"], ["Milton Keynes MK1 1AA"])

    def test_unknown_geography_stays_for_review(self):
        self.assertEqual(self.clean.county_evidence({"advertisedAddress": "Buckinghamshire"}, {})["status"], "needs_location_review")
        self.assertEqual(self.clean.county_evidence({"advertisedAddress": "Penn HP10 8BP"}, {"HP108BP": {"result": None}})["status"], "needs_location_review")

    def test_cached_postcode_responses_are_not_fetched_twice(self):
        response = {"status": 200, "result": [{"query": "HP108BP", "result": {"postcode": "HP10 8BP", "codes": {"admin_district": "E06000060"}}}]}
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return json.dumps(response).encode()
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(self.clean.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = Reply()
                first = self.clean.lookup_postcodes(Path(temporary), ["HP108BP"])
                second = self.clean.lookup_postcodes(Path(temporary), ["HP108BP"])
                self.assertEqual(first, second)
                self.assertEqual(opener.return_value.open.call_count, 1)

    def test_cleaning_existing_exports_rechecks_scope_and_keeps_agent_notes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "exports").mkdir()
            records = [{"name": "Kip McGrath Aylesbury Tuition Centre", "advertisedAddress": "HP10 8BP", "status": "research"},
                       {"name": "Primary School Tutor in Beaconsfield", "advertisedAddress": "HP10 8BP", "status": "research",
                        "provenance": [{"lane": "codex_oauth", "notes": "Older newsletter advert; verify current activity.",
                                        "servicesClaim": ["11+ tuition"], "localityClaim": "Beaconsfield"}]}]
            (root / "exports/businesses.json").write_text(json.dumps(records))
            geography = {"HP108BP": {"result": {"codes": {"admin_district": "E06000060"}}}}
            with patch.object(self.clean, "lookup_postcodes", return_value=geography):
                summary = self.clean.clean_exports(root)
            self.assertEqual(summary["countyListingsResearch"], 1)
            self.assertEqual(summary["heldOrSourceOnly"], 1)
            import csv
            with (root / "exports/bucks-listings-research.csv").open() as stream:
                row = next(csv.DictReader(stream))
            self.assertIn("Older newsletter advert", row["verificationNotes"])
            self.assertEqual(row["currentActivityVerified"], "False")
            self.assertEqual(row["servicesClaim"], "11+ tuition")


class RunnerHealthTests(unittest.TestCase):
    def setUp(self):
        module_spec = importlib.util.spec_from_file_location("pathway_run", MODULE.with_name("run.py"))
        self.runner = importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules, {"discovery": discovery}):
            module_spec.loader.exec_module(self.runner)

    def test_empty_search_is_not_treated_as_an_outage_or_retried(self):
        response = {"status_code": 20000, "tasks": [{"status_code": 40102}]}
        self.assertFalse(self.runner.provider_is_failure(response, {"status": "failed"}))
        self.assertTrue(self.runner.provider_is_failure(None, {"status": "uncertain"}))
        self.assertTrue(self.runner.provider_is_failure({"status_code": 20000, "tasks": [{"status_code": 40101}]}, {"status": "failed"}))

    def test_directory_fallback_changes_semantics_and_only_follows_a_verified_engine_error(self):
        item = {"lane": "directories", "payload": {"keyword": '"Penn" (tutor OR tuition) (site:one.example OR site:two.example)', "depth": 20}}
        response = {"status_code": 20000, "tasks": [{"status_code": 40101}]}
        payload = self.runner.directory_fallback(item, response)
        self.assertEqual(payload["keyword"], '"Penn" (tutor OR tuition) site:one.example')
        self.assertNotEqual(discovery.request_key("directories", item["payload"]), discovery.request_key("directories", payload))
        self.assertIsNone(self.runner.directory_fallback(item, None))
        self.assertIsNone(self.runner.directory_fallback(item, {"status_code": 20000, "tasks": [{"status_code": 40102}]}))

    def test_live_owner_does_not_hide_sdk_or_paid_uncertainty(self):
        now = discovery.parse_time("2026-10-11T10:00:00+00:00")
        value = {"phase": "running", "processLive": True, "observedAt": now.isoformat(),
                 "budget": {"uncertainCents": 100}, "sdk": {"phase": "failed"}}
        health, issues = self.runner.monitor_health(value, now, False)
        self.assertEqual(health, "degraded")
        self.assertEqual(len(issues), 2)

    def test_sdk_startup_stall_is_detected_while_other_paths_progress(self):
        now = discovery.parse_time("2026-10-11T10:06:00+00:00")
        value = {"phase": "running", "processLive": True, "observedAt": now.isoformat(),
                 "budget": {}, "sdk": {"phase": "running", "startedAt": "2026-10-11T10:00:00+00:00"}}
        health, issues = self.runner.monitor_health(value, now, False)
        self.assertEqual(health, "degraded")
        self.assertIn("five minutes", issues[0])

    def test_resuming_an_expired_job_never_extends_its_original_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_text("[]")
            manifest = {"runId": "old-run", "planSha256": discovery.file_hash(root / "plan.json")}
            job = dict(manifest, startedAt="2020-01-01T00:00:00+00:00", deadline="2020-01-01T06:00:00+00:00")
            discovery.atomic_json(root / "manifest.json", manifest)
            discovery.atomic_json(root / "job.json", job)
            self.assertEqual(self.runner.initialize_job(root), job)


class ProviderContractTests(unittest.TestCase):
    def test_paid_response_is_cached_and_never_submitted_twice(self):
        payload = {"keyword": "11 plus tutor Penn", "depth": 100}
        key = discovery.request_key("maps", payload)
        response = {"status_code": 20000, "cost": 0.002, "tasks": [{"id": "task-one", "cost": 0.002,
                    "status_code": 20000, "data": {"tag": "bucks-pathways-" + key[:24]}, "result": []}]}
        class Reply:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def read(self, size): return json.dumps(response).encode()
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            with patch.dict(discovery.os.environ, {"DATAFORSEO_LOGIN": "test", "DATAFORSEO_PASSWORD": "test"}), patch.object(discovery.urllib.request, "build_opener") as opener:
                opener.return_value.open.return_value = Reply()
                self.assertIsNotNone(discovery.provider_request(cache, "maps", payload))
                self.assertIsNotNone(discovery.provider_request(cache, "maps", payload))
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(cache.usage()["spentCents"], 1)

    def test_lost_http_response_is_never_resubmitted(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            with patch.dict(discovery.os.environ, {"DATAFORSEO_LOGIN": "test", "DATAFORSEO_PASSWORD": "test"}), patch.object(discovery.urllib.request, "build_opener") as opener:
                opener.return_value.open.side_effect = OSError("timeout")
                payload = {"keyword": "primary tutor Penn", "depth": 100}
                self.assertIsNone(discovery.provider_request(cache, "maps", payload))
                self.assertIsNone(discovery.provider_request(cache, "maps", payload))
                self.assertEqual(opener.return_value.open.call_count, 1)
                self.assertEqual(cache.usage()["uncertainCents"], 100)


if __name__ == "__main__":
    unittest.main()
