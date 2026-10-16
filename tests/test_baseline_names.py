import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json, file_hash
from baseline_names import verified_name_overlay
from new_leads import classify_delta, run


class BaselineNameTests(unittest.TestCase):
    def fixture(self, root, platform='findtutors.co.uk', name='Haider Ali'):
        if platform == 'findtutors.co.uk':
            url = 'https://www.findtutors.co.uk/tutors-aylesbury/maths-123'
            text = f'''<section id="sectionaddetail"><div class="info">
                <span itemprop="name">{name}</span><div id="line_location">Aylesbury</div>
                <span class="subject">Maths Lessons</span></div>
                <div id="logo_academy_link_profile" class="non-academy"></div>
                <input id="iduser" value="42"></section>'''
        else:
            url = 'https://www.childcare.co.uk/profile/42'
            text = f'''<div class="profile-header--info-wrapper"><h1>{name}</h1>
                <h3>Private Tutor in Aylesbury</h3></div><span class="tag positive">Maths Tutor</span>'''
        body = text.encode()
        sha = hashlib.sha256(body).hexdigest()
        key = hashlib.sha256(url.encode()).hexdigest()
        page = root / 'pages' / (key + '.html')
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_bytes(body)
        receipt = root / 'receipts' / (key + '.json')
        atomic_json(receipt, {'url': url, 'sha256': sha, 'status': 200})
        old = {'canonicalId': 'tutor:old', 'name': name, 'aliases': [name.split()[0].lower(), name.lower()],
               'memberKeys': [platform + ':42'], 'sourceUrls': [url], 'professionalNameHint': None,
               'pageEvidence': [{'url': url, 'sha256': sha}], 'outreachEligible': False}
        current = {'canonicalId': 'tutor:new', 'name': 'Haider Nasir', 'aliases': ['haider', 'haider nasir'],
                   'memberKeys': ['findtutors.co.uk:99'], 'sourceUrls': ['https://www.findtutors.co.uk/tutors-aylesbury/new-999'],
                   'professionalNameHint': 'haider nasir', 'services': ['Maths'], 'outreachEligible': False}
        return old, current, page, receipt

    def test_owned_full_name_is_proved_without_mutating_frozen_inventory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for platform in ('findtutors.co.uk', 'childcare.co.uk'):
                with self.subTest(platform=platform):
                    old, current, _, _ = self.fixture(root, platform)
                    before = json.dumps(old, sort_keys=True)
                    overlay, proof = verified_name_overlay([old], root)
                    self.assertEqual(overlay[0]['professionalNameHint'], 'haider ali')
                    self.assertEqual(json.dumps(old, sort_keys=True), before)
                    self.assertEqual(proof['verifiedNames'], 1)
                    self.assertEqual(proof['httpRequests'], 0)
                    self.assertEqual(classify_delta([old], [current])['new'], [])
                    self.assertEqual(classify_delta(overlay, [current])['new'], [current])

    def test_changed_or_missing_evidence_cannot_release_a_first_name_hold(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, current, page, receipt = self.fixture(root)
            changes = [dict(old, pageEvidence=[]), dict(old, memberKeys=['findtutors.co.uk:777']),
                       dict(old, name='Haider Smith'), dict(old, memberKeys=['findtutors.co.uk:42', 'findtutors.co.uk:99']),
                       dict(old, sourceUrls=old['sourceUrls'] + ['https://www.findtutors.co.uk/tutors-aylesbury/other-456'])]
            for changed in changes:
                overlay, _ = verified_name_overlay([changed], root)
                self.assertIsNone(overlay[0]['professionalNameHint'])
                self.assertEqual(classify_delta(overlay, [current])['new'], [])
            page.write_bytes(page.read_bytes() + b'<!-- changed -->')
            overlay, _ = verified_name_overlay([old], root)
            self.assertIsNone(overlay[0]['professionalNameHint'])
            self.fixture(root)
            data = json.loads(receipt.read_text()); data['sha256'] = '0' * 64
            atomic_json(receipt, data)
            self.assertIsNone(verified_name_overlay([old], root)[0][0]['professionalNameHint'])
            receipt.unlink()
            self.assertIsNone(verified_name_overlay([old], root)[0][0]['professionalNameHint'])

    def test_incomplete_own_name_neighbour_and_conflicting_link_stay_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, _, page, receipt = self.fixture(root, name='Haider')
            self.assertIsNone(verified_name_overlay([old], root)[0][0]['professionalNameHint'])
            old, _, page, receipt = self.fixture(root)
            body = page.read_bytes().replace(b'Haider Ali', b'Haider') + b'<aside><h1>Haider Ali</h1></aside>'
            sha = hashlib.sha256(body).hexdigest(); page.write_bytes(body)
            atomic_json(receipt, {'url': old['sourceUrls'][0], 'sha256': sha, 'status': 200})
            old['pageEvidence'][0]['sha256'] = sha
            self.assertIsNone(verified_name_overlay([old], root)[0][0]['professionalNameHint'])
            old, _, page, receipt = self.fixture(root)
            body = page.read_bytes().replace(b'<span itemprop="name">', b'<a id="lnkProfile" href="/tutor/haider-smith.htm"></a><span itemprop="name">')
            sha = hashlib.sha256(body).hexdigest(); page.write_bytes(body)
            atomic_json(receipt, {'url': old['sourceUrls'][0], 'sha256': sha, 'status': 200})
            old['pageEvidence'][0]['sha256'] = sha
            self.assertIsNone(verified_name_overlay([old], root)[0][0]['professionalNameHint'])

    def test_identical_full_name_known_member_and_unresolved_alias_remain_held(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, current, _, _ = self.fixture(root)
            overlay, _ = verified_name_overlay([old], root)
            same_name = dict(current, name='Haider Ali', aliases=['haider'], professionalNameHint='haider ali')
            self.assertEqual(classify_delta(overlay, [same_name])['new'], [])
            same_member = dict(current, memberKeys=old['memberKeys'])
            self.assertEqual(classify_delta(overlay, [same_member])['known'], [same_member])
            unknown = dict(old, canonicalId='tutor:unresolved', name='Haider', memberKeys=['findtutors.co.uk:888'])
            self.assertEqual(classify_delta(overlay + [unknown], [current])['new'], [])

    def test_final_delta_and_campaign_export_use_the_same_verified_baseline(self):
        from campaign_watch import aggregate_contacts
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles = root / 'profile-cache'
            old, current, _, _ = self.fixture(profiles)
            baseline = root / 'baseline/contacts.json'
            atomic_json(baseline, [old]); baseline_hash = file_hash(baseline)
            atomic_json(root / 'manifest.json', {'profileRunDir': str(profiles)})
            atomic_json(root / 'audit/this-stage-delta/new.json', [current])
            value = aggregate_contacts(root, {'outputDirectory': str(root / 'audit')})
            self.assertEqual(value['delta']['newAcceptedResearchIdentities'], 1)
            self.assertEqual(value['delta']['baselineSha256'], baseline_hash)
            self.assertEqual(json.loads((root / 'exports/additional-contacts/new.json').read_text()), [current])
            self.assertEqual(file_hash(baseline), baseline_hash)
            self.assertEqual(value['baselineNames']['verifiedNames'], 1)
            direct = run(baseline, root / 'exports/additional-contacts.json', root / 'direct', profile_root=profiles)
            self.assertEqual(direct['newAcceptedResearchIdentities'], 1)

    def test_periodic_audit_applies_the_overlay_to_both_baseline_comparisons(self):
        from watch_expansion import watch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles, paid = root / 'profiles', root / 'paid'
            old, current, _, _ = self.fixture(profiles)
            baseline = paid / 'baseline/contacts.json'
            atomic_json(baseline, [old])
            atomic_json(paid / 'manifest.json', {'budgetCents': 100, 'priorExposureCents': 0, 'towns': []})
            def audited(_root, output, *_args, **_kwargs):
                atomic_json(output / 'tutors.json', [current])
                return {'acceptedIdentities': 1}
            with patch('watch_expansion.audit', side_effect=audited), patch('watch_expansion.write_seed_batch', return_value={'urls': 0}):
                result = watch(paid, profiles, baseline, baseline, new_only=True)
            self.assertEqual(result['delta']['newAcceptedResearchIdentities'], 1)
            self.assertEqual(result['thisStageDelta']['newAcceptedResearchIdentities'], 1)
            self.assertEqual(result['delta']['baselineSha256'], file_hash(baseline))
            self.assertEqual(json.loads(baseline.read_text())[0]['professionalNameHint'], None)


if __name__ == '__main__':
    unittest.main()
