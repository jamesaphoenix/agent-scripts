import importlib.util
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways/directory_profiles.py"
spec = importlib.util.spec_from_file_location("directory_profiles", PATH)
directory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(directory)


class ProfileTests(unittest.TestCase):
    def test_verified_rural_places_are_local_without_accepting_ambiguous_names(self):
        for town in ["Flackwell Heath", "Bledlow Ridge", "Hughenden Valley", "Little Kimble", "Long Crendon", "Stewkley", "Stokenchurch", "Woburn Sands"]:
            self.assertEqual(directory.locality_status(town), "advertised_bucks_locality")
        for town in ["Bourne End", "Lane End", "St Albans", "Reading"]:
            self.assertEqual(directory.locality_status(town), "outside_or_unresolved")

    def test_tutordex_uses_the_private_tutor_header_and_numeric_identity(self):
        html = '''<h1>Tutor Profile</h1><div><h1>Catherine</h1><p class="uppercase">Private
          <a href="/uk/academic/subject/english">English</a> Tutor in
          <a href="/uk/academic/town/aylesbury">Aylesbury</a></p></div>'''
        url = "https://tutordex.net/tutor/78/catherine"
        row = directory.profile_record(html, url)
        self.assertEqual((row["name"], row["personId"], row["localityClaim"]), ("Catherine", "78", "Aylesbury"))
        self.assertEqual(row["services"], ["English"])
        self.assertFalse(row["outreachEligible"])
        self.assertIsNone(directory.profile_record(html.replace("Private", "Online"), url))
        self.assertIsNone(directory.profile_record(html, "https://tutordex.net/uk/academic/town/aylesbury"))

    def test_tutorextra_subject_table_and_header_ignore_global_navigation_and_ad_title(self):
        html = '''<h1>Qualified teacher with experience</h1><nav><span>Maths</span><span>English</span></nav>
          <div class="name-type"><div class="fullname">Sky Rogers - Tutor in High Wycombe</div>
          <div class="usertype">Tutor, Teacher</div></div>
          <div class="address"><select class="select-address"><option>High Wycombe, England</option></select></div>
          <table class="table responsive-table"><tr><th>Subject</th><th>Age/level and price</th></tr>
          <tr><td>English Literature lessons</td><td>Primary Age - £35</td></tr></table>'''
        url = "https://tutorextra.co.uk/profile/example-tuition/50-64971"
        row = directory.profile_record(html, url)
        self.assertEqual((row["name"], row["personId"], row["localityClaim"]), ("Sky Rogers", "64971", "High Wycombe"))
        self.assertEqual(row["services"], ["English Literature lessons"])
        self.assertFalse(row["outreachEligible"])
        outside = directory.profile_record(html.replace("High Wycombe", "Reading"), url)
        self.assertEqual(directory.profile_locality_status(outside), "outside_or_unresolved")
        conflict = directory.profile_record(html.replace("</option>", "</option><option>Murphy, NC</option>", 1), url)
        self.assertTrue(conflict["locationConflict"])
        self.assertIsNone(directory.profile_record(html.replace("Tutor, Teacher", "Learning Centre"), url))
        company = directory.profile_record(html.replace("Qualified teacher with experience", "Online education company"), url)
        self.assertEqual(directory.exclusion_reason(company), "institution_or_chain")

    def test_merging_adverts_preserves_all_page_evidence_and_later_location_conflicts(self):
        first = {"name": "Jane", "personId": "42", "platform": "tutorextra.co.uk", "localityClaim": "High Wycombe",
                 "services": ["Maths"], "sourceUrls": ["https://tutorextra.co.uk/profile/maths/50-42"],
                 "pageEvidence": [{"url": "https://tutorextra.co.uk/profile/maths/50-42", "sha256": "a"}]}
        second = dict(first, sourceUrls=["https://tutorextra.co.uk/profile/english/50-42"], locationConflict=True,
                      pageEvidence=[{"url": "https://tutorextra.co.uk/profile/english/50-42", "sha256": "b"}])
        row = directory.merge_profiles([first, second, first])[0]
        self.assertTrue(row["locationConflict"])
        self.assertEqual(len(row["pageEvidence"]), 2)
        self.assertEqual(len(row["sourceUrls"]), 2)

    def test_new_directory_cards_retain_each_person_and_primary_location(self):
        html = '''<div class="user-content-table"><div class="name"><a href="/profile/math-teacher/50-42">Jane</a></div>
          <div class="usertype"><span>Maths</span> Tutor</div><div class="address"><span class="address-top"><span class="sm">Reading, England</span></span></div></div>
          <div class="user-content-table"><div class="name"><a href="/profile/english-teacher/50-43">Jo</a></div>
          <div class="usertype"><span>English</span> Teacher</div><div class="address"><span class="address-top"><span class="sm">High Wycombe, England</span></span></div></div>'''
        rows = directory.listing_records(html, "https://tutorextra.co.uk/search/maths-lessons-in/high-wycombe")
        self.assertEqual([r["personId"] for r in rows], ["42", "43"])
        self.assertEqual([directory.profile_locality_status(r) for r in rows], ["outside_or_unresolved", "advertised_bucks_locality"])
        html = '''<div class="lg:col-span-2"><h2>Catherine</h2><p class="uppercase">English Teacher in Aylesbury</p>
          <a href="/tutor/78/catherine">View this tutor</a></div>'''
        self.assertEqual(directory.listing_records(html, "https://tutordex.net/uk/academic/town/aylesbury/english")[0]["personId"], "78")
        for url in ["https://tutordex.net/api/tutors", "https://tutordex.net/private/forum", "https://tutorextra.co.uk/login", "https://tutorextra.co.uk/profile/x/50-42/reviews"]:
            self.assertIsNone(directory.admitted_url(url))
        self.assertTrue(directory.local_directory_link("https://tutordex.net/uk/academic/town/aylesbury/english/gcse", "https://tutordex.net/uk/academic/town/aylesbury"))
        self.assertFalse(directory.local_directory_link("https://tutordex.net/uk/academic/town/london/english", "https://tutordex.net/uk/academic/town/aylesbury"))
        self.assertFalse(directory.local_directory_link("https://tutorextra.co.uk/search/maths-lessons-in/london", "https://tutorextra.co.uk/search/maths-lessons-in/high-wycombe"))

    def test_page_selection_balances_hosts_and_prioritises_people_without_losing_pages(self):
        first_index = "https://www.findtutors.co.uk/buckinghamshire-tutors/"
        first_person = "https://www.findtutors.co.uk/tutors-aylesbury/maths-123"
        other_index = "https://www.childcare.co.uk/search/Private-Tutors/Buckinghamshire"
        other_person = "https://www.childcare.co.uk/profile/456"
        state = {"queue": [first_index, first_person, other_index, other_person]}
        self.assertEqual(directory.next_page(state), first_person)
        # The last host is durable state, so a resumed process retains fairness.
        state = json.loads(json.dumps(state))
        self.assertEqual(directory.next_page(state), other_person)
        self.assertEqual(directory.next_page(state), first_index)
        self.assertEqual(directory.next_page(state), other_index)
        self.assertEqual(state["queue"], [])

    def test_all_sources_receive_slots_when_three_hosts_have_backlogs(self):
        import collections
        import urllib.parse
        hosts = ["www.findtutors.co.uk", "tutorperch.com", "www.childcare.co.uk"]
        state = {"queue": ["https://" + host + "/robots.txt?page=" + str(index) for host in hosts for index in range(20)]}
        selected = [directory.next_page(state) for _ in range(9)]
        counts = collections.Counter(urllib.parse.urlsplit(url).hostname for url in selected)
        self.assertEqual(counts, {host: 3 for host in hosts})
        self.assertEqual(len(state["queue"]), 51)

    def test_childcare_extracts_tutor_header_subjects_without_trusting_county_breadcrumbs(self):
        html = '''<div class="profile-header--info-wrapper"><h1>Jane Example</h1>
          <h3>Private Tutor in Chiltern (also available for babysitting)</h3></div>
          <div class="tag positive maths-tutor"><span>Maths Tutor</span></div>
          <div class="tag positive 11-plus"><span>11 Plus</span></div>
          <div class="tag positive dbs-check"><span>DBS Check</span></div>
          <script type="application/ld+json">[{"@type":"BreadcrumbList","itemListElement":[
            {"name":"Private Tutors in Buckinghamshire","item":"https://www.childcare.co.uk/search/Private-Tutors/Buckinghamshire"}]}]</script>
          <script type="application/ld+json">{"@type":"Organization","name":"Childcare Limited","address":"London"}</script>'''
        row = directory.profile_record(html, "https://www.childcare.co.uk/profile/123")
        self.assertEqual(row["name"], "Jane Example")
        self.assertEqual(row["personId"], "123")
        self.assertEqual(row["localityClaim"], "Chiltern")
        self.assertEqual(row["directoryCountyCategory"], "Buckinghamshire")
        self.assertEqual(row["services"], ["Maths Tutor", "11 Plus"])
        self.assertFalse(row["outreachEligible"])
        self.assertEqual(directory.profile_locality_status(row), "advertised_bucks_locality")
        outside = directory.profile_record(html.replace("in Chiltern", "in Edinburgh"), "https://www.childcare.co.uk/profile/123")
        self.assertEqual(directory.profile_locality_status(outside), "outside_or_unresolved")
        unknown = directory.profile_record(html.replace("in Chiltern", "in Unknown Place"), "https://www.childcare.co.uk/profile/123")
        self.assertEqual(directory.profile_locality_status(unknown), "outside_or_unresolved")
        self.assertEqual(directory.locality_status("Chiltern"), "outside_or_unresolved")
        self.assertIsNone(directory.profile_record(html.replace("Private Tutor in", "Parent in"), "https://www.childcare.co.uk/profile/123"))
        self.assertIsNone(directory.profile_record(html, "https://www.childcare.co.uk/search/Private-Tutors/Buckinghamshire"))

    def test_childcare_directory_cannot_supply_county_proof_for_a_person(self):
        html = '''<div class="search-result" data-screenname="Jane" data-user-id="123">
          <div class="distance"><span>Private Tutor in Edinburgh</span></div></div>
          <div class="search-result" data-screenname="Jo" data-user-id="124">
          <div class="distance"><span>Private Tutor in Milton Keynes</span></div></div>'''
        url = "https://www.childcare.co.uk/search/Private-Tutors/Buckinghamshire"
        rows = directory.listing_records(html, url)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["localityClaim"], "Edinburgh")
        self.assertEqual(directory.profile_locality_status(rows[0]), "outside_or_unresolved")
        self.assertNotIn("countyClaim", rows[0])
        self.assertTrue(directory.local_directory_link(url + "/2", url))
        self.assertFalse(directory.local_directory_link(url + "/Maths-Tutor", url))
        self.assertFalse(directory.local_directory_link("https://www.childcare.co.uk/search/Private-Tutors/London/2", url))
        for path in ["/profile/123/reviews/456", "/login?ret=/profile/123", "/search/Parents/Buckinghamshire", "/message/123"]:
            self.assertIsNone(directory.admitted_url("https://www.childcare.co.uk" + path))

    def test_county_search_does_not_make_online_or_neighbouring_tutors_local(self):
        cards = """
        <div class="itemv3" data-link="/tutors-aylesbury/maths-123">
          <div class="username">Jane Example</div><div class="place">Aylesbury, Bierton</div>
          <div class="subject">Maths</div><h3>Maths tuition</h3></div>
        <div class="itemv3" data-link="/online-maths/maths-124">
          <div class="username">Online Tutor</div><div class="place">Online lessons</div>
          <div class="subject">Maths</div></div>
        <div class="itemv3" data-link="/tutors-maidenhead/maths-125">
          <div class="username">Another Tutor</div><div class="place">Maidenhead, Burnham</div>
          <div class="subject">Maths</div></div>"""
        rows = directory.listing_records(cards, "https://www.findtutors.co.uk/buckinghamshire-tutors/")
        self.assertEqual(len(rows), 3)
        self.assertEqual([directory.locality_status(r["localityClaim"]) for r in rows],
                         ["advertised_bucks_locality", "unresolved", "outside_or_unresolved"])

    def test_person_identity_deduplicates_multiple_adverts(self):
        base = {"name": "Jane Example", "localityClaim": "Aylesbury", "services": ["Maths"],
                "sourceUrls": ["https://www.findtutors.co.uk/tutors-aylesbury/maths-123"],
                "platform": "findtutors.co.uk", "personId": "42"}
        second = dict(base, services=["English"], sourceUrls=["https://www.findtutors.co.uk/tutors-aylesbury/english-124"])
        merged = directory.merge_profiles([base, second])
        self.assertEqual(len(merged), 1)
        self.assertEqual(set(merged[0]["services"]), {"Maths", "English"})
        self.assertEqual(len(merged[0]["sourceUrls"]), 2)
        other = dict(base, personId="43", name="Jane")
        self.assertEqual(len(directory.merge_profiles([base, other])), 2)

    def test_tutorperch_uses_the_person_and_not_the_platform_registered_office(self):
        html = '''<script type="application/ld+json">{"@type":"Organization","name":"Tutorperch",
          "address":"London"}</script><script type="application/ld+json">{"@type":"ProfilePage",
          "mainEntity":{"@type":"Person","name":"Jo","jobTitle":"Tutor","areaServed":{"name":"Bancroft"},
          "knowsAbout":["11+ English Language","GCSE English"]}}</script>'''
        row = directory.profile_record(html, "https://tutorperch.com/t/jo-rutw")
        self.assertEqual(row["name"], "Jo")
        self.assertEqual(row["localityClaim"], "Bancroft")
        self.assertIn("11+ English Language", row["services"])
        self.assertFalse(row["outreachEligible"])
        self.assertIsNone(directory.profile_record(html, "https://tutorperch.com/11-plus/buckinghamshire"))

    def test_institutions_and_non_tuition_lessons_are_held(self):
        for name, subjects in [("Example Academy", ["Maths"]), ("Aylesbury Grammar School", ["Maths"]),
                               ("Private Person", ["Yoga"]), ("Kumon", ["Maths"])]:
            with self.subTest(name=name):
                self.assertIsNotNone(directory.exclusion_reason({"name": name, "services": subjects}))
        self.assertIsNone(directory.exclusion_reason({"name": "Jane Example", "services": ["French", "GCSE Maths"]}))

    def test_urls_cannot_escape_public_read_only_directory_paths(self):
        for url in ["http://127.0.0.1/", "https://www.findtutors.co.uk.example/tutor/x.htm",
                    "https://user:password@tutorperch.com/t/jo-rutw", "https://tutorperch.com:123/t/jo-rutw",
                    "https://tutorperch.com/api/", "https://www.findtutors.co.uk/booking/123"]:
            self.assertIsNone(directory.admitted_url(url))
        self.assertEqual(directory.admitted_url("https://tutorperch.com/t/jo-rutw?utm_source=share#about"),
                         "https://tutorperch.com/t/jo-rutw")

    def test_failed_fetches_and_body_hashes_are_durable(self):
        with tempfile.TemporaryDirectory() as temporary:
            cache = directory.PageCache(Path(temporary), spacing=0)
            with patch.object(cache, "download", side_effect=OSError("offline")) as download:
                self.assertIsNone(cache.fetch("https://www.findtutors.co.uk/buckinghamshire-tutors/"))
                self.assertIsNone(cache.fetch("https://www.findtutors.co.uk/buckinghamshire-tutors/"))
                self.assertEqual(download.call_count, 1)
            with patch.object(cache, "download", return_value=(b"<h1>Profile</h1>", "text/html", 200)):
                url = "https://tutorperch.com/t/jo-rutw"
                self.assertIsNotNone(cache.fetch(url))
            cache.body_path(url).write_bytes(b"changed")
            with self.assertRaisesRegex(ValueError, "hash changed"):
                cache.fetch(url)

    def test_directory_recursion_stays_in_the_county_and_retains_pagination(self):
        source = "https://www.findtutors.co.uk/buckinghamshire-tutors/"
        self.assertTrue(directory.local_directory_link(source + "?page=2", source))
        self.assertTrue(directory.local_directory_link("https://www.findtutors.co.uk/tutors-aylesbury/", source))
        self.assertFalse(directory.local_directory_link("https://www.findtutors.co.uk/tutors-london/", source))
        self.assertFalse(directory.local_directory_link("https://tutorperch.com/t/jo-rutw", source))

    def test_malformed_person_metadata_does_not_stop_other_pages(self):
        html = '<script type="application/ld+json">{"@type":"ProfilePage","mainEntity":[]}</script>'
        self.assertIsNone(directory.profile_record(html, "https://tutorperch.com/t/jo-rutw"))

    def test_verified_tutor_name_is_not_replaced_by_a_breadcrumb(self):
        html = '''<section id="sectionaddetail"><div class="profile"><div class="info"><div class="line1">
          <div id="logo_academy_link_profile" class="non-academy"><a id="lnkProfile" href="/tutor/jane-example.htm">
          <span class="user-name">Jane</span><span>View profile</span></a></div></div>
          <div id="line_location">High Wycombe, Naphill</div><span class="subject">Maths Lessons</span></div></div>
          <ul id="ariadna"><li><span itemprop="name">Find Tutors</span></li><li><span itemprop="name">Maths</span></li>
          <li><span itemprop="name">High Wycombe</span></li></ul><input id="iduser" value="42"></section>'''
        row = directory.profile_record(html, "https://www.findtutors.co.uk/tutors-high-wycombe/maths-123")
        self.assertEqual(row["name"], "Jane")
        self.assertEqual(row["localityClaim"], "High Wycombe, Naphill")

    def test_tutorperch_card_location_limits_profile_following(self):
        html = '''<div data-slot="card"><a class="card-overlay-link" href="/t/jo-rutw" aria-label="View Jo's profile"></a>
          <span><svg class="lucide-map-pin"></svg>Bancroft</span><ul><li>English Language</li></ul></div>
          <div data-slot="card"><a class="card-overlay-link" href="/t/chris-4nvk" aria-label="View Chris's profile"></a>
          <span><svg class="lucide-map-pin"></svg>Cambridge</span><ul><li>Maths</li></ul></div>'''
        url = "https://tutorperch.com/find/results?level=11-plus&mode=online"
        rows = directory.listing_records(html, url)
        self.assertEqual([r["name"] for r in rows], ["Jo", "Chris"])
        self.assertEqual([directory.locality_status(r["localityClaim"]) for r in rows],
                         ["advertised_bucks_locality", "outside_or_unresolved"])
        self.assertFalse(directory.local_directory_link("https://tutorperch.com/find/results", url))
        self.assertTrue(directory.local_directory_link(url + "&page=2", url))

    def test_source_batches_are_hash_bound_deduplicated_and_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            inbox = root / "seed-batches"
            inbox.mkdir()
            url = "https://tutorperch.com/t/jo-rutw"
            body = json.dumps({"urls": [url, url, "https://tutorperch.com/messages/new"]}).encode()
            digest = hashlib.sha256(body).hexdigest()
            (inbox / (digest + ".json")).write_bytes(body)
            state = {"queue": [], "seen": []}
            directory.import_seed_batches(root, state)
            directory.import_seed_batches(root, state)
            self.assertEqual(state["queue"], [url])
            self.assertEqual(state["importedSeedBatches"], [digest])
            wrong = inbox / ("a" * 64 + ".json")
            wrong.write_bytes(body)
            with self.assertRaisesRegex(ValueError, "Seed batch hash changed"):
                directory.import_seed_batches(root, state)
