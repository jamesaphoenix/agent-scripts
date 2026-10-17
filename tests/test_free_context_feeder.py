import hashlib
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import free_context_feeder as feeder


class FreeContextFeederTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.runtime = Path(self.tmp.name)
        self.control = self.runtime / 'state/tutor-studio-20261017-tutor-graph-16h'
        self.qual = self.runtime / 'state/tutor-qualification-20261017-tutor-graph-16h'
        self.control.mkdir(parents=True)
        self.qual.mkdir(parents=True)
        auth = {
            'instruction': 'Go go for another 10-20 hours.',
            'precedingInstruction': 'Could you continually improving the crawl and only adding extra tutor sites to our graph.',
            'requestedAt': '2026-10-17T17:46:36+00:00', 'deadline': '2026-10-18T09:46:36+00:00',
            'durationHours': 16, 'newMoneyAuthorizedCents': 0,
            'maximumRemainingSpendNano': 3798775900, 'sharedCombinedCeilingUsd': 15,
            'paidDataForSeoSearchesAllowed': False, 'decisionsClassificationAllowed': True,
            'maximumSdkTurns': 0, 'anthropicApiKeyAllowed': False, 'outreachAllowed': False,
            'monitorIntervalSeconds': 1800,
            'scopeSha256': '626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6',
            'previousQualificationRoot': '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/tutor-qualification-20261017-free-12h',
            'previousProfileRoot': '/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/bucks-private-tutors-20261017-free-12h',
        }
        (self.control / 'authorization.json').write_text(json.dumps(auth))
        crawler = self.runtime / 'crawler'; crawler.write_text('pinned')
        job = {'sourceDir': str(Path(feeder.__file__).resolve().parent),
               'authorizedDeadline': '2026-10-18T09:46:36+00:00',
               'deadlineUnix': 1792316796.0, 'crawler': str(crawler),
               'crawlerSha256': hashlib.sha256(b'pinned').hexdigest(),
               'blockedUrls': ['http://www.held.example.com/']}
        (self.qual / 'job.json').write_text(json.dumps(job))
        self.seeds = self.control / 'free-context/seed-batches'
        self.seeds.mkdir(parents=True)

    def seed(self, urls):
        raw = json.dumps({'schemaVersion': 1, 'sourceUrl': 'https://public.example.org/list', 'urls': urls}).encode()
        (self.seeds / (hashlib.sha256(raw).hexdigest() + '.json')).write_bytes(raw)

    def test_free_fetch_saves_hashes_and_never_qualifies(self):
        url = 'https://novel-tutor.example.com/'
        self.seed([url])
        evidence = {'url': url, 'text': 'A local Buckinghamshire maths tutor offers one-to-one lessons.',
                    'fetchCheckpointSha256': 'a' * 64}
        with patch.object(feeder, 'fetch', return_value=evidence) as fetch:
            report = feeder.run(self.runtime)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(report['states'], {'succeeded': 1})
        self.assertEqual(report['providerCalls'], 0)
        with closing(sqlite3.connect(self.control / 'free-context/pages.sqlite')) as db:
            row = db.execute('SELECT text_sha256,checkpoint_sha256 FROM pages').fetchone()
        self.assertEqual(row, (hashlib.sha256(evidence['text'].encode()).hexdigest(), 'a' * 64))
        self.assertFalse((self.qual / 'exports/automated-qualification/accepted.json').exists())

    def test_held_url_and_post_deadline_seed_make_no_requests(self):
        self.seed(['https://held.example.com/', 'https://fresh-tutor.example.com/'])
        with patch.object(feeder.time, 'time', return_value=1792316796.0), patch.object(feeder, 'fetch') as fetch:
            report = feeder.run(self.runtime)
        fetch.assert_not_called()
        self.assertEqual(report['states'], {'pending': 1})
        self.assertGreaterEqual(report['rejectedInputs'], 1)


if __name__ == '__main__':
    unittest.main()
