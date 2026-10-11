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

