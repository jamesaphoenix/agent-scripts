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
configuration_spec = importlib.util.spec_from_file_location("configuration", MODULE.with_name("configuration.py"))
configuration = importlib.util.module_from_spec(configuration_spec)
configuration_spec.loader.exec_module(configuration)


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

    def test_launcher_uses_requested_run_directory(self):
        launcher = self.load_launcher()
        root = Path("/private/state/tutor-lead-pathways-evening")
        command = launcher.runner_command(root)
        self.assertEqual(command[-2:], ["--run-dir", str(root)])


class RunConfigurationTests(unittest.TestCase):
    def settings(self, manifest):
        module_spec = importlib.util.spec_from_file_location("pathway_configuration", MODULE.with_name("configuration.py"))
        configuration = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(configuration)
        return configuration.launchd_settings(manifest)

    def test_new_run_monitor_has_its_own_owner_and_fifteen_minute_schedule(self):
        labels = {"run": "com.jud.tutor-lead-pathways-20261011-evening",
                  "watch": "com.jud.tutor-lead-pathways-watch-20261011-evening"}
        self.assertEqual(self.settings({"launchdLabels": labels, "monitorIntervalSeconds": 900}), (labels, 900))

    def test_legacy_manifests_preserve_their_original_monitor(self):
        labels, interval = self.settings({})
        self.assertEqual(labels["run"], "com.jud.tutor-lead-pathways-20261011")
        self.assertEqual(interval, 1800)

    def test_cross_run_labels_and_invalid_intervals_are_rejected(self):
        labels = {"run": "com.jud.tutor-lead-pathways-first", "watch": "com.jud.tutor-lead-pathways-watch-second"}
        for manifest in [{"launchdLabels": labels}, {"monitorIntervalSeconds": True}, {"monitorIntervalSeconds": 0}]:
            with self.subTest(manifest=manifest), self.assertRaises(ValueError):
                self.settings(manifest)

    def test_completed_source_evidence_survives_a_fresh_paid_cache(self):
        with tempfile.TemporaryDirectory() as temporary:
            old, root = Path(temporary) / "previous", Path(temporary) / "next"
            discovery.atomic_json(old / "sdk/results/slot-00.json", {"leads": [{
                "name": "Named primary tutor", "website": "https://namedtutor.co.uk/", "email": None,
                "phone": None, "localityClaim": "Amersham", "services": ["Primary tuition"],
                "sourceUrls": ["https://namedtutor.co.uk/"], "notes": "Historical advert; activity unknown"}]})
            discovery.atomic_json(old / "crawls/0001/crawl.json", {"results": [{
                "node": {"url": "https://namedtutor.co.uk/about"}, "fetchedAt": "2026-10-10T10:00:00Z",
                "evidence": {"url": "https://namedtutor.co.uk/about", "title": "Named tutor",
                    "text": "Primary tuition and 11 plus tutoring", "emails": ["hello@namedtutor.co.uk"]}}]})
            discovery.atomic_json(root / "manifest.json", {"historicalEvidenceRoots": [str(old)]})
            cache = discovery.PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            rows = discovery.collect_records(cache, root)
            self.assertEqual(len(rows), 2)
            self.assertTrue(all(row["provenance"][0]["historical"] for row in rows))
            self.assertIn("activity unknown", rows[0]["provenance"][0]["notes"])
            self.assertEqual(rows[1]["provenance"][0]["observedAt"], "2026-10-10T10:00:00Z")
            summary = discovery.write_exports(root, cache)
            self.assertEqual(summary["newResearchIdentities"], 0)
            self.assertEqual(summary["newCrawlerEvidencePages"], 0)


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

    def test_facebook_profile_ids_do_not_merge_different_tutors(self):
        rows = [discovery.business_record({"name": "Tutored by Tara", "category": "Tutor", "place_id": "tara-place",
                                          "website": "https://www.facebook.com/profile.php?id=123456789&ref=page_internal"}, {}),
                discovery.business_record({"name": "French with RB", "category": "Tutor", "place_id": "rb-place",
                                          "website": "https://www.facebook.com/profile.php?id=987654321"}, {})]
        self.assertEqual(len(discovery.merge_businesses(rows)), 2)
        self.assertEqual(discovery.website_key(rows[0]["website"]),
                         discovery.website_key("https://m.facebook.com/profile.php?id=123456789"))
        self.assertNotEqual(discovery.website_key("https://www.facebook.com/people/First-Tutor/123456789/"),
                            discovery.website_key("https://www.facebook.com/people/Second-Tutor/987654321/"))

    def test_facebook_group_posts_are_sources_but_named_profiles_remain_research(self):
        for url in ["https://www.facebook.com/groups/11plusinfo/posts/123/",
                    "https://m.facebook.com/groups/123/", "https://www.facebook.com/share/p/123/"]:
            self.assertEqual(discovery.hold_reason({"name": "11+ tutor recommendations", "website": url}), "source_directory_or_job_page")
        for url in ["https://www.facebook.com/profile.php?id=123", "https://www.facebook.com/individualtutor/"]:
            self.assertIsNone(discovery.hold_reason({"name": "Independent Tutor", "website": url}))

    def test_observed_non_tutor_county_categories_are_held(self):
        for name, category in [("Joy Hawes Watercolours", "Artist"), ("Supply Tutoring Ltd", "Recruiter"),
                               ("Apricot Therapies", "Hypnotherapy service"), ("Perform Amersham", "Drama school")]:
            self.assertEqual(discovery.hold_reason({"name": name, "category": category}), "outside_academic_tuition_scope")
        for name, category in [("Chepping View SCITT Primary Teacher Training", "Teachers college"),
                               ("Acorn Montessori Nursery and Preschool", "Pre-school")]:
            self.assertEqual(discovery.hold_reason({"name": name, "category": category}), "school_or_college")
        self.assertIsNone(discovery.hold_reason({"name": "Marie Hayes Primary Maths Tuition", "category": "Educational institution"}))

    def test_manual_named_candidates_do_not_inherit_shared_directory_identity_or_email_verification(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = discovery.PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            folder = root / "review/candidates"
            folder.mkdir(parents=True)
            source = "https://www.haddenham.net/services-page-6.html"
            data = {"observedAt": "2026-10-11T13:40:00+00:00", "leads": [
                {"name": "First Tutor", "website": None, "phone": "07958627536", "email": "first@example.org",
                 "sourceUrls": [source], "localityClaim": "Dinton", "services": ["11 Plus tuition"], "notes": "Advert from 2018."},
                {"name": "Second Tutor", "website": None, "phone": "07585579677", "email": "second@example.org",
                 "sourceUrls": [source], "localityClaim": "Address unstated", "services": ["Year 3 mathematics and 11+"], "notes": "Directory claim."}]}
            (folder / "community.json").write_text(json.dumps(data))
            merged = discovery.merge_businesses(discovery.collect_records(cache, root))
            self.assertEqual(len(merged), 2)
            for row in merged:
                self.assertIsNone(row["website"])
                self.assertEqual(row["sourceUrl"], source)
                self.assertEqual(row["emails"], [])
                self.assertFalse(row["contactClaims"][0]["verified"])
                self.assertFalse(row["outreachEligible"])
                self.assertIsNone(discovery.hold_reason(row))
                self.assertEqual(row["provenance"][0]["lane"], "manual_source_review")
                self.assertEqual(discovery.subject_hint(row), "11_plus_hint")
            self.assertEqual(discovery.hold_reason({"name": "Community directory", "website": source}), "source_directory_or_job_page")

    def test_manual_address_and_source_notes_reach_county_cleaning_without_approving_contact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = discovery.PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            folder = root / "review/candidates"
            folder.mkdir(parents=True)
            data = {"observedAt": "2026-10-11T13:40:00+00:00", "leads": [
                {"name": "Known Tutor", "website": "https://tutor.example/", "phone": None, "email": None,
                 "advertisedAddress": "Named venue, Haddenham HP17 8AJ", "sourceUrls": ["https://tutor.example/locations/"],
                 "localityClaim": "Haddenham venue", "services": ["11+ groups"], "notes": "Venue claim, no current booking check."}]}
            (folder / "venues.json").write_text(json.dumps(data))
            discovery.write_exports(root, cache)
            rows=json.loads((root / "exports/businesses.json").read_text())
            self.assertEqual(rows[0]["advertisedAddress"], "Named venue, Haddenham HP17 8AJ")
            self.assertEqual(rows[0]["provenance"][0]["notes"], data["leads"][0]["notes"])
            self.assertFalse(rows[0]["reviewed"])
            self.assertFalse(rows[0]["independentVerified"])
            self.assertEqual(rows[0]["subjectEvidence"], "11_plus_hint")

    def test_pmt_profiles_and_directory_contacts_keep_separate_source_identities(self):
        rows = [discovery.business_record({"name": "Samuel W. Private Tutor", "website": "https://www.pmt.education/tutor/tutor-profile/samuel-w-226582"}, {}),
                discovery.business_record({"name": "Another tutor", "website": "https://www.pmt.education/tutor/tutor-profile/another-tutor-123456"}, {}),
                discovery.business_record({"name": "About PMT", "website": "https://www.pmt.education/about-us"}, {})]
        self.assertEqual(len(discovery.merge_businesses(rows)), 3)
        self.assertTrue(all(r["status"] == "held" and r["holdReason"] == "source_directory_or_job_page" for r in rows))

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

    def test_verified_brain_up_and_clara_james_franchise_brands_are_held(self):
        for row in [{"name": "Brain Up Abacus Mental Maths Academy", "category": "Corporate office"},
                    {"name": "Mental maths", "website": "https://www.brainup.uk/"},
                    {"name": "Mental maths", "website": "https://www.brainupabacus.co.uk/"},
                    {"name": "Clara James Tutoring", "category": "Tutor"},
                    {"name": "Private tutor", "website": "https://clarajamestutoring.co.uk/"}]:
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

    def test_broad_maps_businesses_require_tuition_evidence(self):
        for name, category, website in [
            ("Sterling Grove Accountants Limited", "Chartered accountant", "https://www.sgaweb.co.uk/"),
            ("Sunday Tablewear", "Ceramic manufacturer", "https://sunday-tablewear.square.site/"),
            ("A Bit of Support", "IT support and services", "https://abitofsupport.co.uk/"),
            ("Best of You Consultancy", "Coaching center", "https://www.bestofyouconsultancy.co.uk/"),
            ("NDA First Aid Training Ltd", "Training provider", "https://www.ndatraining.co.uk/"),
        ]:
            with self.subTest(name=name):
                self.assertEqual(discovery.hold_reason({"name": name, "category": category, "website": website}), "needs_tuition_evidence")
        for row in [
            {"name": "Named tutor", "category": "Private tutor"},
            {"name": "Named teacher", "category": "Educational consultant"},
            {"name": "Marie Hayes Primary Maths Tuition", "category": "Educational institution"},
            {"name": "THE RYE COLLEGE LTD", "category": "Private tutor"},
            {"name": "Alison Taylor", "category": "After-school programme", "website": "https://alisontaylor11plustuition.co.uk/"},
            {"name": "Named Maths Tuition", "category": "Corporate office"},
            {"name": "Language tutor", "category": "Language school"},
            {"name": "Tracy Stewart Tutoring", "category": None},
        ]:
            with self.subTest(control=row):
                self.assertIsNone(discovery.hold_reason(row))

    def test_actual_broad_maps_non_academic_and_school_categories_are_held(self):
        for category in ["Pilates studio", "Swimming school", "Swimming instructor", "Dance school", "Tennis instructor",
                         "Music school", "Guitar instructor", "Gym", "Yoga studio", "Yoga instructor", "Optometrist",
                         "Physiotherapist", "Reflexologist", "Nursing home", "Cooking school", "Aviation training institute"]:
            with self.subTest(category=category):
                self.assertEqual(discovery.hold_reason({"name": "Named listing", "category": category}), "outside_academic_tuition_scope")
        self.assertEqual(discovery.hold_reason({"name": "Optotutor", "category": "Optometrist", "siteEvidence": [{"elevenPlusHint": True}]}), "outside_academic_tuition_scope")
        for category in ["Girls' Secondary School", "Girls’ Secondary School", "Faith school", "Community school", "Vocational school"]:
            with self.subTest(category=category):
                self.assertEqual(discovery.hold_reason({"name": "Named institution", "category": category}), "school_or_college")

    def test_coding_franchises_are_held(self):
        for name in ["Computer Xplorers South Buckinghamshire", "ComputerXplorers", "Code Ninjas Aylesbury"]:
            self.assertEqual(discovery.hold_reason({"name": name, "category": "Learning Centre"}), "recognised_chain")

    def test_tuition_evidence_is_checked_after_merging_provider_categories(self):
        rows = [discovery.business_record({"name": "Jo Smith", "category": "Corporate office", "place_id": "jo"}, {"lane": "business"}),
                discovery.business_record({"name": "Jo Smith", "category": "Private tutor", "place_id": "jo"}, {"lane": "maps"})]
        merged = discovery.merge_businesses(rows)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["categories"], ["Corporate office", "Private tutor"])
        self.assertIsNone(merged[0]["holdReason"])
        self.assertIsNone(discovery.hold_reason(merged[0]))
        self.assertEqual(merged[0]["status"], "research")
        unrelated = discovery.merge_businesses([discovery.business_record({"name": "Sunday Tablewear", "category": "Ceramic manufacturer"}, {})])
        self.assertEqual(unrelated[0]["holdReason"], "needs_tuition_evidence")
        self.assertEqual(unrelated[0]["status"], "held")

    def test_independent_schools_and_unrelated_maps_businesses_are_held(self):
        for name, category in [("Caldicott Preparatory School", "Independent school"),
                               ("Thorpe House School", "Independent school"),
                               ("Pipers Corner School", "Sixth form college"),
                               ("Limes Montessori", "Montessori preschool")]:
            with self.subTest(category=category, name=name):
                self.assertEqual(discovery.hold_reason({"name": name, "category": category}), "school_or_college")
        for category in ("Personal trainer", "Music Teacher", "Media company", "Bag shop", "Golf club"):
            with self.subTest(category=category):
                self.assertEqual(discovery.hold_reason({"name": "Saved Maps listing", "category": category}), "outside_academic_tuition_scope")
        self.assertIsNone(discovery.hold_reason({"name": "Primary School Tutor in Beaconsfield", "category": "Tutor"}))

    def test_changing_plan_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "plan.json").write_text("[]")
            job = {"planSha256": discovery.file_hash(root / "plan.json")}
            discovery.validate_plan(root, job)
            (root / "plan.json").write_text("[1]")
            with self.assertRaises(ValueError):
                discovery.validate_plan(root, job)

    def test_grammar_school_references_do_not_label_a_gcse_tutor_as_eleven_plus(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = discovery.PaidCache(root, 2000, 0)
            self.addCleanup(cache.db.close)
            folder = root / "crawls/batch-000"
            folder.mkdir(parents=True)
            url = "https://gcse-tutor.example/meet-your-tutor"
            evidence = {"url": url, "title": "GCSE Maths Tutor", "text": "Maths tuition for Years 7 to 11. Students attend local grammar schools.",
                        "elevenPlus": True, "primary": False, "contentHash": "saved-page-hash"}
            (folder / "crawl.json").write_text(json.dumps({"results": [{"node": {"url": url}, "evidence": evidence}]}))
            discovery.write_exports(root, cache)
            row = json.loads((root / "exports/businesses.json").read_text())[0]
            self.assertEqual(row["subjectEvidence"], "needs_subject_verification")
            self.assertFalse(row["siteEvidence"][0]["elevenPlusHint"])
            self.assertTrue(row["siteEvidence"][0]["admissionsHint"])
            self.assertEqual(row["siteEvidence"][0]["contentHash"], "saved-page-hash")
            self.assertFalse(row["outreachEligible"])

    def test_explicit_eleven_plus_page_text_remains_a_hint_without_provider_flag(self):
        for phrase in ("11+", "11 +", "11-plus", "11 plus", "eleven-plus", "elevenplus"):
            with self.subTest(phrase=phrase), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                cache = discovery.PaidCache(root, 2000, 0)
                try:
                    folder = root / "crawls/batch-000"
                    folder.mkdir(parents=True)
                    url = "https://individual-tutor.example/"
                    evidence = {"url": url, "title": "Individual tutor", "text": f"Private tutor offering {phrase} tuition.",
                                "elevenPlus": False, "primary": False}
                    (folder / "crawl.json").write_text(json.dumps({"results": [{"node": {"url": url}, "evidence": evidence}]}))
                    discovery.write_exports(root, cache)
                    row = json.loads((root / "exports/businesses.json").read_text())[0]
                    self.assertEqual(row["subjectEvidence"], "11_plus_hint")
                    self.assertFalse(row["elevenPlusVerified"])
                    self.assertFalse(row["outreachEligible"])
                finally:
                    cache.db.close()


class CountyCleaningTests(unittest.TestCase):
    def setUp(self):
        module_spec = importlib.util.spec_from_file_location("county_clean", MODULE.with_name("clean.py"))
        self.clean = importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules, {"discovery": discovery, "configuration": configuration}):
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

    def test_agent_profiles_without_postcodes_are_exported_with_unverified_claims_and_holds(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "exports").mkdir()
            records = [{"name": "Wendy", "website": "https://www.childcare.co.uk/profile/3678407", "status": "research",
                        "provenance": [{"lane": "codex_oauth", "localityClaim": "Bourne End", "servicesClaim": ["11+ tuition"], "notes": "Individual professional profile."}]},
                       {"name": "Actualize Tutoring", "website": "https://www.bark.com/en/gb/company/actualize-tutoring/lpbYY/", "status": "research",
                        "provenance": [{"lane": "codex_oauth", "localityClaim": "Chesham", "notes": "Professional reported unavailable."}]}]
            (root / "exports/businesses.json").write_text(json.dumps(records))
            with patch.object(self.clean, "lookup_postcodes", return_value={}):
                summary = self.clean.clean_exports(root)
            self.assertEqual(summary["countyListingsResearch"], 0)
            self.assertEqual(summary["agentCandidateIdentities"], 2)
            self.assertEqual(summary["agentCandidatesHeld"], 1)
            import csv
            with (root / "exports/agent-researched-candidates.csv").open() as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(rows[0]["localityClaim"], "Bourne End")
            self.assertEqual(rows[0]["countyStatus"], "needs_location_review")
            self.assertEqual(rows[0]["currentActivityVerified"], "False")
            self.assertEqual(rows[1]["status"], "held")
            self.assertIn("unavailable", rows[1]["verificationNotes"])


class SearchPlanTests(unittest.TestCase):
    def load_prepare(self):
        module_spec = importlib.util.spec_from_file_location("pathway_prepare", MODULE.with_name("prepare.py"))
        prepare = importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules, {"discovery": discovery, "configuration": configuration}):
            module_spec.loader.exec_module(prepare)
        return prepare

    def test_plain_town_searches_feed_both_maps_and_organic_without_narrow_operators(self):
        prepare = self.load_prepare()
        towns = [{"name": "High Wycombe", "coordinate": "51.629,-0.749"},
                 {"name": "Amersham", "coordinate": "51.677,-0.607"}]
        plan = prepare.build_plan(towns)
        self.assertEqual(len({item["key"] for item in plan}), len(plan))
        for town in towns:
            for lane in ("maps", "organic"):
                for prefix in ("tutor", "11%2B tutor"):
                    matches = [item for item in plan if item["lane"] == lane and item["town"] == town["name"]
                               and item["payload"]["keyword"] == f"{prefix} {town['name']} Buckinghamshire"]
                    self.assertEqual(len(matches), 1)
                    self.assertEqual(matches[0]["payload"]["depth"], 100 if lane == "maps" else 20)
        self.assertTrue(all("+" not in item["payload"].get("keyword", "") for item in plan))

    def test_completed_ledgers_reuse_receipts_and_charge_prior_spend_once(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary)
            payload = {"keyword": "tutor High Wycombe Buckinghamshire", "depth": 20}
            envelope = {"status_code": 20000, "cost": 0.048, "tasks": [{"id": "original-task", "cost": 0.048, "status_code": 20000}]}
            first = discovery.PaidCache(history / "tutor-lead-pathways-first", 2000, 228)
            self.addCleanup(first.db.close)
            key = discovery.request_key("organic", payload)
            first.reserve(key, "organic", payload, 100)
            first.settle(key, envelope)
            (first.root / "runtime.json").write_text(json.dumps({"phase": "complete"}))
            second = discovery.PaidCache(history / "tutor-lead-pathways-second", 2000, 233)
            self.addCleanup(second.db.close)
            second.import_receipt("organic", payload, envelope)
            (second.root / "runtime.json").write_text(json.dumps({"phase": "complete"}))
            prior, receipts = self.load_prepare().completed_paid_history(history, 0)
            self.assertEqual(prior, 233)
            target = discovery.PaidCache(history / "target", 2000, prior)
            self.addCleanup(target.db.close)
            for lane, saved_payload, response in receipts:
                target.import_receipt(lane, saved_payload, response)
            self.assertEqual(target.usage()["importedReceipts"], 1)
            self.assertFalse(target.reserve(key, "organic", payload, 100))

    def test_active_uncertain_or_tampered_history_is_not_silently_rebought(self):
        for state in ("running", "uncertain", "tampered"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                history = Path(temporary)
                cache = discovery.PaidCache(history / "tutor-lead-pathways-source", 2000, 228)
                self.addCleanup(cache.db.close)
                payload = {"keyword": "tutor Amersham"}
                key = discovery.request_key("organic", payload)
                cache.reserve(key, "organic", payload, 100)
                if state == "uncertain":
                    cache.mark_uncertain(key, "unverified response")
                else:
                    cache.settle(key, {"status_code": 20000, "cost": 0.002, "tasks": [{"id": "task", "cost": 0.002, "status_code": 20000}]})
                (cache.root / "runtime.json").write_text(json.dumps({"phase": "running" if state == "running" else "complete"}))
                if state == "tampered":
                    Path(cache.get(key)["receipt"]).write_text("{}")
                with self.assertRaises(ValueError):
                    self.load_prepare().completed_paid_history(history, 0)

    def test_prior_exposure_rejects_a_ledger_charge_that_disagrees_with_its_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary)
            cache = discovery.PaidCache(history / "tutor-lead-pathways-source", 2000, 228)
            self.addCleanup(cache.db.close)
            payload = {"keyword": "tutor High Wycombe"}
            key = discovery.request_key("organic", payload)
            cache.reserve(key, "organic", payload, 100)
            cache.settle(key, {"status_code": 20000, "cost": 0.048, "tasks": [{"id": "task", "cost": 0.048, "status_code": 20000}]})
            (cache.root / "runtime.json").write_text(json.dumps({"phase": "complete"}))
            cache.db.execute("UPDATE requests SET cost_cents=0 WHERE key=?", (key,))
            cache.db.commit()
            with self.assertRaises(ValueError):
                self.load_prepare().completed_paid_history(history, 0)

    def test_parallel_completed_runs_keep_the_inherited_floor_and_both_new_charges(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary)
            for name, cost in [("first", 0.048), ("second", 0.071)]:
                cache = discovery.PaidCache(history / ("tutor-lead-pathways-" + name), 2000, 228)
                self.addCleanup(cache.db.close)
                payload = {"keyword": name}
                key = discovery.request_key("organic", payload)
                cache.reserve(key, "organic", payload, 100)
                cache.settle(key, {"status_code": 20000, "cost": cost, "tasks": [{"id": name, "cost": cost, "status_code": 20000}]})
                (cache.root / "runtime.json").write_text(json.dumps({"phase": "complete"}))
            prior, _ = self.load_prepare().completed_paid_history(history, 0)
            self.assertEqual(prior, 228 + 5 + 8)


class RunnerHealthTests(unittest.TestCase):
    def setUp(self):
        module_spec = importlib.util.spec_from_file_location("pathway_run", MODULE.with_name("run.py"))
        self.runner = importlib.util.module_from_spec(module_spec)
        with patch.dict(sys.modules, {"discovery": discovery, "configuration": configuration}):
            module_spec.loader.exec_module(self.runner)

    def test_monitor_records_launcher_startup_without_creating_a_new_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            discovery.atomic_json(root / "manifest.json", {"monitorIntervalSeconds": 900})
            with patch.object(self.runner.subprocess, "run") as command:
                self.runner.watch(root)
            command.assert_not_called()
            report = json.loads((root / "monitor/latest.json").read_text())
            self.assertEqual(report["health"], "starting")
            self.assertEqual(report["monitorIntervalSeconds"], 900)
            self.assertFalse((root / "job.json").exists())

    def test_larger_new_run_can_revisit_history_without_resetting_its_own_lineage(self):
        with tempfile.TemporaryDirectory() as temporary:
            history = Path(temporary)
            discovery.atomic_json(history / "tracelearn-tutors-old/discovery.json", {"crawl": {"results": [
                {"node": {"url": "https://knowntutor.co.uk/"}, "evidence": {"title": "Tutor"}}]}})
            legacy = {"historyRoot": str(history)}
            self.assertEqual(self.runner.seed_previous_urls(legacy), {"https://knowntutor.co.uk/"})
            expanded = dict(legacy, historicalEvidenceRoots=[str(history / "completed")],
                            crawlLimits={"pagesPerSite": 8})
            self.assertEqual(self.runner.seed_previous_urls(expanded), set())

    def test_recorded_county_practices_precede_unknown_and_outside_seed_records(self):
        with tempfile.TemporaryDirectory() as temporary:
            old = Path(temporary)
            (old / "exports").mkdir()
            (old / "exports/bucks-listings-research.csv").write_text("name,website\nLocal tutor,https://localtutor.co.uk/\n")
            (old / "exports/outside-recorded-area.csv").write_text("name,website\nOther tutor,https://othertutor.co.uk/\n")
            records = [{"name": "Other tutor", "website": "https://othertutor.co.uk/"},
                       {"name": "Unknown tutor", "website": "https://unknowntutor.co.uk/"},
                       {"name": "Local tutor", "website": "https://localtutor.co.uk/about"}]
            ordered = self.runner.prioritise_seed_records(records, {"historicalEvidenceRoots": [str(old)]})
            self.assertEqual([row["name"] for row in ordered], ["Local tutor", "Unknown tutor", "Other tutor"])
            self.assertEqual(len(ordered), 3)
            self.assertNotIn("localVerified", ordered[0])

    def test_paid_admission_checks_the_deadline_at_each_primary_or_fallback_call(self):
        with patch.object(self.runner.time, "time", return_value=100), patch.object(self.runner, "provider_request", return_value={"status_code": 20000}) as request:
            self.assertEqual(self.runner.request_before_deadline(None, "directories", {"keyword": "first"}, 101), {"status_code": 20000})
            self.assertIsNone(self.runner.request_before_deadline(None, "directories", {"keyword": "fallback"}, 100))
            request.assert_called_once_with(None, "directories", {"keyword": "first"})

    def test_resume_rejects_a_changed_paid_cutoff_even_when_final_window_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            discovery.atomic_json(root / "plan.json", [])
            manifest = {"planSha256": discovery.file_hash(root / "plan.json")}
            discovery.atomic_json(root / "manifest.json", manifest)
            discovery.atomic_json(root / "job.json", dict(manifest,
                startedAt="2026-10-11T10:00:00Z", deadline="2026-10-11T16:00:00Z",
                discoveryDeadline="2026-10-11T16:00:00Z"))
            with self.assertRaisesRegex(ValueError, "paid research cutoff"):
                self.runner.initialize_job(root)

    def cache_business_page(self, cache, offset, total, status=20000):
        payload = {"categories": ["private_tutor"], "location_coordinate": "51.572,-0.497,12", "limit": 100, "offset": offset}
        key = discovery.request_key("business", payload)
        self.assertTrue(cache.reserve(key, "business", payload, 100))
        cache.settle(key, {"status_code": 20000, "cost": 0.048, "tasks": [{"id": "page-" + str(offset), "cost": 0.048,
                           "status_code": status, "result": [{"total_count": total, "items": [{"type": "business_listing"}]}]}]})
        return key, payload

    def test_business_pagination_continues_only_from_a_saved_successful_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            parent, payload = self.cache_business_page(cache, 100, 432)
            state = {"completedQueries": []}
            item = self.runner.next_business_supplement(cache, state)
            self.assertEqual(item["parentKey"], parent)
            self.assertEqual(item["payload"], dict(payload, offset=200))
            self.assertEqual(item["key"], discovery.request_key("business", dict(payload, offset=200)))
            self.assertIsNone(cache.get(item["key"]))
            state["businessSupplementaryQueries"] = [{"parentKey": parent, "key": item["key"]}]
            self.assertIsNone(self.runner.next_business_supplement(cache, state))

    def test_business_extra_pages_never_replay_failed_requests_or_exceed_bounds(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            self.cache_business_page(cache, 100, 432)
            self.cache_business_page(cache, 200, 432, status=40101)
            self.cache_business_page(cache, 400, 999)
            self.assertIsNone(self.runner.next_business_supplement(cache, {}))
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            self.cache_business_page(cache, 100, 432)
            self.assertIsNone(self.runner.next_business_supplement(cache, {"businessSupplementaryQueries": [{}] * 20}))

    def test_exhausted_business_pages_do_not_consume_another_paced_query_slot(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            first, payload = self.cache_business_page(cache, 0, 80)
            skipped_payload = dict(payload, offset=100)
            skipped = {"lane": "business", "key": discovery.request_key("business", skipped_payload), "payload": skipped_payload}
            fresh_payload = dict(payload, location_coordinate="51.800,-0.600,12")
            fresh = {"lane": "business", "key": discovery.request_key("business", fresh_payload), "payload": fresh_payload}
            state = {"completedQueries": [first]}
            selected = self.runner.next_query("business", [skipped, fresh], cache, state)
            self.assertEqual(selected, fresh)
            self.assertIn(skipped["key"], state["completedQueries"])
            self.assertEqual(state["skippedQueries"][0]["key"], skipped["key"])
            self.assertIsNone(cache.get(skipped["key"]))

    def test_reused_successful_and_failed_searches_do_not_consume_new_paid_slots(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = discovery.PaidCache(Path(temporary), 200, 0)
            self.addCleanup(cache.db.close)
            plan = []
            for keyword, status in [('known', 20000), ('failed', 40101), ('new', None)]:
                payload = {'keyword': keyword}
                key = discovery.request_key('organic', payload)
                plan.append({'lane': 'organic', 'key': key, 'payload': payload})
                if status is not None:
                    cache.reserve(key, 'organic', payload, 100)
                    cache.settle(key, {'status_code': 20000, 'cost': 0.002, 'tasks': [{'id': keyword, 'cost': 0.002, 'status_code': status}]})
            state = {'completedQueries': []}
            self.assertEqual(self.runner.next_query('organic', plan, cache, state), plan[2])
            self.assertEqual(state['completedQueries'], [plan[0]['key'], plan[1]['key']])

    def test_crawler_enriches_tutor_categories_and_subject_urls_without_name_keywords(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [discovery.business_record({"name": "Peasy Learning", "category": "Tutor", "website": "https://peasylearning.example/"}, {}),
                    discovery.business_record({"name": "Katie Bucks Eleven Plus", "category": "Private tutor", "website": "https://buckselevenplus.example/"}, {}),
                    discovery.business_record({"name": "Alison Taylor", "category": "After-school programme", "website": "https://alisontaylor11plustuition.example/"}, {}),
                    discovery.business_record({"name": "Tuition bag shop", "category": "Bag shop", "website": "https://tuition-bags.example/"}, {})]
            state = {"crawlRoots": [], "crawlNumber": 0}
            job = {"freePageBudget": 4000, "crawlerBinary": "/reviewed/tutor-crawler"}
            permitted = [row["website"] for row in rows[:3]]
            with patch.object(self.runner, "collect_records", return_value=rows), patch.object(self.runner.subprocess, "Popen"), patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({"permitted": permitted, "rejected": []}), "")):
                result = self.runner.start_crawl(root, job, None, state, set())
            self.assertIsNotNone(result)
            self.assertEqual((root / "crawls/batch-000/seeds.txt").read_text().splitlines(),
                             ["https://peasylearning.example/", "https://buckselevenplus.example/", "https://alisontaylor11plustuition.example/"])
            self.assertEqual(state["crawlNumber"], 1)

    def test_rejected_roots_do_not_launch_an_empty_crawl_or_hide_next_valid_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            urls = [f"https://www.facebook.com/independenttutor{i}/" for i in range(12)]
            rows = [discovery.business_record({"name": "Independent Tutor", "website": url}, {}) for url in urls]
            rows.append(discovery.business_record({"name": "New Tutor", "website": "https://newtutor.example/"}, {}))
            state = {"crawlRoots": [], "crawlNumber": 0}
            rejected = [{"url": url, "reason": "excluded_site"} for url in urls]
            response = {"permitted": ["https://newtutor.example/"], "rejected": rejected}
            with patch.object(self.runner, "collect_records", return_value=rows), patch.object(self.runner.subprocess, "Popen") as launch, patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(response), "")) as policy:
                self.assertIsNotNone(self.runner.start_crawl(root, {"freePageBudget": 4000, "crawlerBinary": "/reviewed/crawler"}, None, state, set()))
            self.assertEqual(policy.call_args.args[0][1], "filter-seeds")
            self.assertEqual((root / "crawls/batch-000/seeds.txt").read_text().splitlines(), ["https://newtutor.example/"])
            self.assertEqual(state["crawlRoots"], ["https://newtutor.example/"])
            self.assertEqual(state["crawlRejected"], urls)
            launch.assert_called_once()

    def test_entirely_rejected_seed_batch_is_saved_without_launching_crawler(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            url = "https://www.yell.com/s/tutoring-haddenham.html"
            row = discovery.business_record({"name": "Tutor directory", "website": url}, {})
            state = {"crawlRoots": [], "crawlNumber": 0}
            response = {"permitted": [], "rejected": [{"url": url, "reason": "excluded_site"}]}
            with patch.object(self.runner, "collect_records", return_value=[row]), patch.object(self.runner.subprocess, "Popen") as launch, patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(response), "")):
                self.assertIsNone(self.runner.start_crawl(root, {"freePageBudget": 4000, "crawlerBinary": "/reviewed/crawler"}, None, state, set()))
            launch.assert_not_called()
            self.assertEqual(state["crawlNumber"], 0)
            self.assertEqual(state["crawlRejected"], [url])
            self.assertEqual(json.loads((root / "crawls/seed-filter-000.json").read_text()), response)

    def test_new_run_uses_its_expanded_limits_and_old_manifest_keeps_original_limits(self):
        expanded = {"depth": 2, "branchSites": 15, "sites": 50, "batchPages": 400,
                    "pagesPerSite": 8, "workers": 4, "rootsPerBatch": 10}
        for limits, expected in [(expanded, {"--depth": "2", "--pages": "400", "--pages-per-site": "8"}),
                                 (None, {"--depth": "1", "--pages": "200", "--pages-per-site": "4"})]:
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                row = discovery.business_record({"name": "Tutor", "website": "https://new-tutor.example/"}, {})
                job = {"freePageBudget": 10000, "crawlerBinary": "/reviewed/crawler"}
                if limits is not None:
                    job["crawlLimits"] = limits
                with patch.object(self.runner, "collect_records", return_value=[row]), patch.object(self.runner.subprocess, "Popen") as launch, patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({"permitted": [row["website"]], "rejected": []}), "")):
                    self.runner.start_crawl(root, job, None, {"crawlRoots": [], "crawlNumber": 0}, set())
                command = launch.call_args.args[0]
                for flag, value in expected.items():
                    self.assertEqual(command[command.index(flag) + 1], value)
                self.assertEqual(command[command.index("--branch-sites") + 1], "15")

    def test_saved_town_serp_result_becomes_a_crawler_seed_without_another_search(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cache = discovery.PaidCache(root, 200, 0)
            self.addCleanup(cache.db.close)
            url = "https://local-tutor.example/11-plus"
            payload = {"keyword": "11%2B tutor High Wycombe Buckinghamshire", "depth": 20}
            envelope = {"status_code": 20000, "cost": 0.002, "tasks": [{"id": "serp-task", "cost": 0.002, "status_code": 20000,
                         "result": [{"items": [{"type": "organic", "title": "Local Eleven Plus Tuition", "url": url}]}]}]}
            cache.import_receipt("organic", payload, envelope)
            with patch.object(self.runner.subprocess, "Popen"), patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({"permitted": [url], "rejected": []}), "")), patch.object(discovery, "provider_request") as provider:
                self.runner.start_crawl(root, {"freePageBudget": 10000, "crawlerBinary": "/reviewed/crawler"}, cache, {"crawlRoots": [], "crawlNumber": 0}, set())
            self.assertEqual((root / "crawls/batch-000/seeds.txt").read_text().splitlines(), [url])
            provider.assert_not_called()
            self.assertEqual(cache.usage()["reportedCostUsd"], 0)

    def test_fetched_external_site_is_not_reintroduced_as_a_new_root_with_reset_depth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "crawls/batch-000"
            folder.mkdir(parents=True)
            (folder / "crawl.json").write_text(json.dumps({"pages": 1, "results": [{"node": {"url": "https://linked-tutor.example/about"}, "evidence": {"url": "https://linked-tutor.example/about"}}]}))
            rows = [discovery.business_record({"name": "Linked Tutor", "website": "https://linked-tutor.example/"}, {}),
                    discovery.business_record({"name": "Fresh Tutor", "website": "https://fresh-tutor.example/"}, {})]
            with patch.object(self.runner, "collect_records", return_value=rows), patch.object(self.runner.subprocess, "Popen"), patch.object(self.runner.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({"permitted": [rows[1]["website"]], "rejected": []}), "")):
                self.runner.start_crawl(root, {"freePageBudget": 4000, "crawlerBinary": "/reviewed/crawler"}, None, {"crawlRoots": [], "crawlNumber": 1}, set())
            self.assertEqual((root / "crawls/seed-candidates.txt").read_text().splitlines(), [rows[1]["website"]])

    def test_pending_frontier_resumes_its_checkpoint_with_preserved_topology_before_new_roots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "crawls/batch-000"
            folder.mkdir(parents=True)
            (folder / "crawl.json").write_text(json.dumps({"pages": 20, "frontier": [{"url": "https://tutor.example/contact"}],
                                                        "config": {"maxPages": 200}, "results": []}))
            state = {"crawlRoots": ["https://tutor.example/"], "crawlNumber": 1}
            with patch.object(self.runner, "collect_records", return_value=[]), patch.object(self.runner.subprocess, "Popen") as launch, patch.object(self.runner.subprocess, "run") as policy:
                result = self.runner.start_crawl(root, {"freePageBudget": 25, "crawlerBinary": "/reviewed/crawler"}, None, state, set())
            self.assertIsNotNone(result)
            command = launch.call_args.args[0]
            self.assertIn("--resume", command)
            self.assertNotIn("--depth", command)
            self.assertNotIn("--seeds", command)
            self.assertEqual(command[command.index("--pages") + 1], "25")
            self.assertEqual(state["crawlNumber"], 1)
            policy.assert_not_called()

    def test_expansion_cannot_disable_existing_hard_bounds_or_exceed_remaining_pages(self):
        for limits in ({"depth": 3}, {"branchSites": 16}, {"pagesPerSite": 9}, {"batchPages": 1001},
                       {"sites": 501}, {"workers": 5}, {"rootsPerBatch": 0}, {"depth": True}, {"unknown": 1}):
            with self.subTest(limits=limits), tempfile.TemporaryDirectory() as temporary:
                with patch.object(self.runner, "collect_records", return_value=[]), patch.object(self.runner.subprocess, "Popen") as launch:
                    with self.assertRaises(ValueError):
                        self.runner.start_crawl(Path(temporary), {"freePageBudget": 4000, "crawlerBinary": "/reviewed/crawler", "crawlLimits": limits}, None, {"crawlRoots": [], "crawlNumber": 0}, set())
                launch.assert_not_called()

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

    def test_crawler_failure_remains_visible_until_a_later_batch_succeeds(self):
        now = discovery.parse_time("2026-10-11T13:20:00+00:00")
        value = {"phase": "running", "processLive": True, "observedAt": now.isoformat(), "budget": {},
                 "recentErrors": [{"at": "2026-10-11T13:18:46+00:00", "lane": "crawler", "error": "missing checkpoint"}]}
        health, issues = self.runner.monitor_health(value, now, False)
        self.assertEqual(health, "degraded")
        self.assertEqual(issues, ["Crawler batch failed; no later successful checkpoint is recorded"])
        value["lastCrawlerSuccessAt"] = "2026-10-11T13:19:00+00:00"
        self.assertEqual(self.runner.monitor_health(value, now, False), ("healthy", []))

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
