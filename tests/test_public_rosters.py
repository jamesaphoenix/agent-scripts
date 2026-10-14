from pathlib import Path
import hashlib
import json
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
from discovery import atomic_json
from decisions_classifier import DecisionsCache
from new_leads import classify_delta
from public_rosters import SOURCE_URL, parse_roster, qualify_cached_rosters
from qualify_inventory import create_database, owned_questions, VERSION
from test_decisions_classifier import response

SCOPE = {'subjects': ['english', 'maths'], 'includeElevenPlus': True,
         'includePrimaryTutors': True, 'excludeMusicOnly': True}


def article(body):
    return '<link rel="canonical" href="' + SOURCE_URL + '"><section data-hook="post-description">' + body + '</section>'


def entry(name, subjects, description='', email=''):
    return f'<p>Name : {name} Subjects : {subjects} Age range: 7-11 Email: {email}</p><p>{description}</p>'


class PublicRosterTests(unittest.TestCase):
    def test_owned_entry_boundaries_duplicate_markup_and_heading_only_locality(self):
        body = '<h1>Marlow Tutors</h1>' + entry('Jane Example', 'English', 'I offer tuition in Marlow.', 'jane@fixturetuition.co.uk')
        body += entry('Other Tutor', 'Music', '', 'other@fixturetuition.co.uk')
        body += entry('Unknown Tutor', 'Maths') + '<p>Good luck in your search.</p><p>Email: editor@fixturetuition.co.uk</p>'
        rows = parse_roster(article(body + body), SCOPE, ['Marlow'])
        self.assertEqual(len(rows), 3)
        jane = next(r for r in rows if r['name'] == 'Jane Example')
        self.assertEqual(jane['emails'], ['jane@fixturetuition.co.uk'])
        self.assertEqual(jane['localityClaim'], 'Marlow')
        self.assertNotIn('other@fixturetuition.co.uk', jane['ownText'])
        self.assertIsNone(next(r for r in rows if r['name'] == 'Unknown Tutor')['localityClaim'])
        self.assertEqual(next(r for r in rows if r['name'] == 'Other Tutor')['matchedSubjects'], [])

    def test_canonical_and_conflicting_duplicate_entries_are_rejected(self):
        with self.assertRaises(ValueError): parse_roster('<p>Name: Jane Example Subjects: Maths</p>', SCOPE, ['Marlow'])
        with self.assertRaises(ValueError): parse_roster(article(entry('Jane Example','Maths') + entry('Jane Example','Music')), SCOPE, ['Marlow'])

    def test_past_school_job_does_not_supply_private_offer_locality(self):
        body = entry('Past Teacher', 'English', 'I taught at a school in Marlow for years. I offer online tuition.')
        self.assertIsNone(parse_roster(article(body), SCOPE, ['Marlow'])[0]['localityClaim'])

    def test_shared_article_url_cannot_merge_different_individuals(self):
        jane = {'canonicalId':'roster:jane','name':'Jane Example','sourceUrls':[SOURCE_URL],
                'sharedSourceUrls':[SOURCE_URL], 'outreachEligible':False}
        sally = dict(jane, canonicalId='roster:sally', name='Sally Example')
        self.assertEqual(classify_delta([jane], [sally])['new'], [sally])
        self.assertEqual(classify_delta([jane], [jane])['known'], [jane])

    def test_cached_source_hash_replay_deadline_and_contact_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); saved = root/'public-rosters'; saved.mkdir()
            key = hashlib.sha256(SOURCE_URL.encode()).hexdigest()
            body = article(entry('Jane Example','English','I offer tuition in Marlow. Call 07700 900123.','jane@fixturetuition.co.uk')
                + entry('Unlocated Tutor','Maths')).encode()
            (saved/(key+'.html')).write_bytes(body)
            atomic_json(saved/(key+'.json'), {'url':SOURCE_URL,'status':200,'sha256':hashlib.sha256(body).hexdigest()})
            db = create_database(root/'candidates.sqlite'); self.addCleanup(db.close)
            cache = Mock(); cache.classify.return_value = {'kind':'independent_tutor','kindProbability':.99,
                'niche':.99,'local':.99,'outside':.01,'decisionKey':'settled','cacheHit':False}
            job = {'towns':['Marlow'],'deadlineUnix':time.time()+60}
            result = qualify_cached_rosters(root, job, db, cache, SCOPE, [])
            self.assertEqual(result['acceptedResearchProfiles'], 1)
            evidence = cache.classify.call_args.args[0]
            self.assertNotIn('jane@fixturetuition.co.uk', evidence['text'])
            self.assertNotIn('07700 900123', evidence['text'])
            export = json.loads((root/'exports/public-roster-qualification/accepted.json').read_text())
            self.assertEqual(export[0]['email'], 'jane@fixturetuition.co.uk')
            self.assertFalse(export[0]['outreachEligible'])
            held = json.loads((root/'exports/public-roster-qualification/held.json').read_text())
            self.assertEqual(held[0]['status'], 'research_held')
            self.assertEqual(cache.classify.call_count, 1)
            cache.reset_mock(); job['deadlineUnix']=time.time()-1
            self.assertEqual(qualify_cached_rosters(root,job,db,cache,SCOPE,[])['state'], 'deadline')
            cache.classify.assert_not_called()
            job['deadlineUnix']=time.time()+60;(saved/(key+'.html')).write_bytes(body+b' ')
            with self.assertRaises(ValueError):qualify_cached_rosters(root,job,db,cache,SCOPE,[])
            cache.classify.assert_not_called()

    def test_shared_bank_restarts_reuse_settled_results_and_preserve_uncertain_holds(self):
        for uncertain in (False, True):
            with self.subTest(uncertain=uncertain), tempfile.TemporaryDirectory() as directory:
                root=Path(directory);saved=root/'public-rosters';saved.mkdir()
                key=hashlib.sha256(SOURCE_URL.encode()).hexdigest()
                body=article(entry('Jane Example','English','I offer tuition in Marlow.')).encode()
                (saved/(key+'.html')).write_bytes(body)
                atomic_json(saved/(key+'.json'),{'url':SOURCE_URL,'status':200,'sha256':hashlib.sha256(body).hexdigest()})
                db=create_database(root/'candidates.sqlite');self.addCleanup(db.close)
                calls=[]
                def transport(payload):
                    calls.append(payload)
                    if uncertain: raise TimeoutError()
                    return response()
                settings={'cap_nano':10_000_000_000,'prior_exposure_cents':10000,
                    'additional_authorization':{'instruction':'Approved test bank','recordedAt':'2026-10-14','additionalCapCents':1000},
                    'question_set':owned_questions(['Marlow'],SCOPE),'prompt_version':VERSION,'transport':transport}
                cache=DecisionsCache(root/'shared-decisions.sqlite',['Marlow'],**settings)
                job={'towns':['Marlow'],'deadlineUnix':time.time()+60}
                first=qualify_cached_rosters(root,job,db,cache,SCOPE,[])
                cost=cache.summary();cache.close()
                restarted=DecisionsCache(root/'shared-decisions.sqlite',['Marlow'],**settings);self.addCleanup(restarted.close)
                second=qualify_cached_rosters(root,job,db,restarted,SCOPE,[])
                self.assertEqual(len(calls),1)
                self.assertEqual(restarted.summary(),cost)
                self.assertEqual(first['acceptedResearchProfiles'],0 if uncertain else 1)
                self.assertEqual(second['settledNewDecisionCalls'],0)


if __name__ == '__main__': unittest.main()
