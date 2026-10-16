import importlib.util
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

PATH = Path(__file__).parents[1] / "agent-loops/tutor-lead-pathways/audit_directory_identities.py"
sys.path.insert(0, str(PATH.parent))
spec = importlib.util.spec_from_file_location("identity_audit", PATH)
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


class IdentityTests(unittest.TestCase):
    def test_linked_name_slug_with_initial_surname_stays_incomplete(self):
        self.assertIsNone(audit.professional_name('https://www.findtutors.co.uk/tutor/alex-p.htm'))
        self.assertEqual(audit.professional_name('https://www.findtutors.co.uk/tutor/alex-parker-5.htm'), 'alex parker')

    def own_name_rows(self, name, platform="findtutors.co.uk", extra=""):
        if platform == "findtutors.co.uk":
            url = "https://www.findtutors.co.uk/tutors-aylesbury/maths-123"
            body = f'''<section id="sectionaddetail"><div class="info">
              <span itemprop="name">{name}</span><div id="line_location">Aylesbury</div>
              <span class="subject">Maths Lessons</span></div>
              <div id="logo_academy_link_profile" class="non-academy"></div>
              <input id="iduser" value="42"></section>{extra}'''.encode()
            person_id = "42"
        else:
            url = "https://www.childcare.co.uk/profile/42"
            body = f'''<div class="profile-header--info-wrapper"><h1>{name}</h1>
              <h3>Private Tutor in Aylesbury</h3></div>
              <span class="tag positive">Maths Tutor</span>{extra}'''.encode()
            person_id = "42"
        row = audit.profile_record(body.decode(), url)
        digest = hashlib.sha256(body).hexdigest()
        row["pageEvidence"] = [{"url": url, "sha256": digest}]
        self.assertEqual(row["personId"], person_id)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "receipts").mkdir()
            (root / "pages").mkdir()
            key = hashlib.sha256(url.encode()).hexdigest()
            (root / "pages" / (key + ".html")).write_bytes(body)
            (root / "receipts" / (key + ".json")).write_text(json.dumps({"url": url, "sha256": digest}))
            return audit.audit_rows(root, [row])

    def test_complete_own_name_without_profile_link_disambiguates_baseline_first_names(self):
        from new_leads import classify_delta
        for platform in ("findtutors.co.uk", "childcare.co.uk"):
            with self.subTest(platform=platform):
                rows = self.own_name_rows("Michael Alan Consden", platform)
                self.assertEqual(rows[0]["professionalNameHint"], "michael alan consden")
                accepted, _, _ = audit.reconcile(rows, {"merges": []})
                baseline = [{"canonicalId": "old", "name": "Michael", "aliases": ["michael", "michael rice"],
                             "professionalNameHint": "michael rice", "sourceUrls": []}]
                self.assertEqual(classify_delta(baseline, accepted)["new"], accepted)
                baseline[0]["professionalNameHint"] = None
                self.assertEqual(classify_delta(baseline, accepted)["new"], [])
                baseline[0]["professionalNameHint"] = "michael alan consden"
                self.assertEqual(classify_delta(baseline, accepted)["new"], [])

    def test_neighbouring_full_names_do_not_expand_an_initial_or_first_name(self):
        for name in ("Michael", "Michael C", "M Consden", "Dr Syeda", "Maths Mentor"):
            with self.subTest(name=name):
                rows = self.own_name_rows(name, extra='<aside><span itemprop="name">Michael Alan Consden</span></aside>')
                self.assertIsNone(rows[0]["professionalNameHint"])

    def test_ambiguous_uk_places_need_explicit_county_context(self):
        self.assertTrue(audit.ambiguous_county("Burnham"))
        self.assertTrue(audit.ambiguous_county("Sands"))
        self.assertTrue(audit.ambiguous_county("Bourton"))
        self.assertFalse(audit.ambiguous_county("Burnham, Buckinghamshire"))
        self.assertFalse(audit.ambiguous_county("Denham (Buckinghamshire)"))
        self.assertFalse(audit.ambiguous_county("Bancroft"))

    def test_the_count_requires_hash_verified_person_and_qualification_evidence(self):
        url = "https://www.findtutors.co.uk/tutors-aylesbury/maths-123"
        body = b'''<section id="sectionaddetail"><div class="info"><span class="user-name">Jane</span>
          <div id="line_location">Aylesbury</div><span class="subject">Maths Lessons</span></div>
          <div id="logo_academy_link_profile" class="non-academy"></div><input id="iduser" value="42"></section>'''
        key = hashlib.sha256(url.encode()).hexdigest()
        digest = hashlib.sha256(body).hexdigest()
        row = {"name": "Jane", "platform": "findtutors.co.uk", "personId": "42", "localityClaim": "Aylesbury",
               "services": ["Maths Lessons"], "sourceUrls": [url], "outreachEligible": False,
               "pageEvidence": [{"url": url, "sha256": digest}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "receipts").mkdir()
            (root / "pages").mkdir()
            page = root / "pages" / (key + ".html")
            page.write_bytes(body)
            (root / "receipts" / (key + ".json")).write_text(json.dumps({"url": url, "sha256": digest}))
            self.assertEqual(len(audit.audit_rows(root, [row])), 1)
            with self.assertRaisesRegex(ValueError, "person identity"):
                audit.audit_rows(root, [dict(row, personId="99")])
            page.write_bytes(body.replace(b"Aylesbury", b"London"))
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                audit.audit_rows(root, [row])

    def row(self, key, name, full=None):
        return {"key": key, "name": name, "professionalNameHint": full, "pageEvidence": [{"url": "https://example.test/" + key, "sha256": "a"}]}

    def test_matching_first_names_with_distinct_own_professional_names_stay_separate(self):
        rows = [self.row("a:1", "Jane", "jane smith"), self.row("b:2", "Jane", "jane brown")]
        accepted, held, groups = audit.reconcile(rows, {"merges": []})
        self.assertEqual(len(accepted), 2)
        self.assertEqual(held, [])
        self.assertEqual(groups, [])

    def test_an_uncertain_match_is_held_and_never_silently_merged(self):
        rows = [self.row("a:1", "Mrs Jane"), self.row("b:2", "Jane")]
        accepted, held, groups = audit.reconcile(rows, {"merges": []})
        self.assertEqual(len(accepted), 1)
        self.assertEqual(len(held), 1)
        self.assertEqual(len(groups), 1)
        self.assertEqual(accepted[0]["memberKeys"], ["a:1"])
        self.assertEqual(held[0]["reason"], "possible_duplicate_requires_review")
        accepted, held, groups = audit.reconcile([self.row("a:1", "Jane"), self.row("b:2", "Jane Smith")], {"merges": []})
        self.assertEqual((len(accepted), len(held)), (1, 1))

    def test_an_incomplete_name_cannot_collapse_two_distinct_professional_names(self):
        rows = [self.row("a:1", "Jane"), self.row("b:2", "Jane", "jane smith"),
                self.row("c:3", "Jane", "jane brown")]
        for ordered in (rows, list(reversed(rows))):
            accepted, held, groups = audit.reconcile(ordered, {"merges": []})
            self.assertEqual({r["key"] for r in accepted}, {"b:2", "c:3"})
            self.assertEqual([r["key"] for r in held], ["a:1"])
            self.assertEqual(len(groups), 1)

    def test_an_exact_name_collision_remains_held_despite_another_distinct_name(self):
        rows = [self.row("a:1", "Jane", "jane smith"), self.row("b:2", "Jane", "jane smith"),
                self.row("c:3", "Jane", "jane brown"), self.row("d:4", "Jane")]
        accepted, held, _ = audit.reconcile(rows, {"merges": []})
        self.assertEqual({r["key"] for r in accepted}, {"a:1", "c:3"})
        self.assertEqual({r["key"] for r in held}, {"b:2", "d:4"})

    def test_a_reviewed_merge_requires_matching_page_hashes_and_preserves_both_sources(self):
        rows = [self.row("a:1", "Jane"), self.row("b:2", "Jane")]
        decision = {"members": ["a:1", "b:2"], "reason": "Matching public professional biographies",
                    "evidence": [e for row in rows for e in row["pageEvidence"]]}
        accepted, held, groups = audit.reconcile(rows, {"merges": [decision]})
        self.assertEqual(len(accepted), 1)
        self.assertEqual(accepted[0]["memberKeys"], ["a:1", "b:2"])
        self.assertEqual(len(accepted[0]["pageEvidence"]), 2)
        self.assertEqual(held, [])
        decision["evidence"][0] = dict(decision["evidence"][0], sha256="changed")
        with self.assertRaisesRegex(ValueError, "evidence"):
            audit.reconcile(rows, {"merges": [decision]})

    def test_aliases_do_not_depend_on_title_or_platform_account_suffix(self):
        self.assertEqual(audit.normalize_name("Dr. Margaret Thomas"), "margaret thomas")
        self.assertEqual(audit.professional_name("https://www.findtutors.co.uk/tutor/margaret-thomas6226261.htm"), "margaret thomas")
        self.assertIsNone(audit.professional_name("https://www.findtutors.co.uk/tutor/jane-123.htm"))
        for claim in ["11+ Maths", "11 Plus", "Eleven-plus English"]:
            self.assertIsNotNone(audit.ELEVEN_PLUS.search(claim))
        self.assertIsNone(audit.ELEVEN_PLUS.search("Primary up to 11 years"))

