import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways/directory_profiles.py"
spec = importlib.util.spec_from_file_location("directory_profiles", PATH)
directory = importlib.util.module_from_spec(spec)
spec.loader.exec_module(directory)


class ProfileTests(unittest.TestCase):
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
