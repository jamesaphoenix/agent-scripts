import json
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent-loops/tutor-lead-pathways'))
import focused_run
from decisions_classifier import APIUnavailable, DecisionsCache
from discovery import atomic_json, file_hash
from focused_frontier import Frontier, Policy
from test_decisions_classifier import response


class FocusedRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / 'stage'
        self.stage.mkdir()
        self.binary = self.root / 'fake-crawler'
        self.binary.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv
assert 'OPENAI_API_KEY' not in os.environ
if args[1]=='link-audit':
 folder=pathlib.Path(args[args.index('--out')+1]); src=json.loads(pathlib.Path(args[args.index('--sources')+1]).read_text())[0]
 (folder/'audit.json').write_text(json.dumps({'pages':[{'source':src,'title':'Jane','text':'I tutor in Amersham. '*15,'links':[]}],'checks':[]}))
 raise SystemExit(0)
checkpoint=pathlib.Path(args[args.index('--state')+1])
url=pathlib.Path(args[args.index('--seeds')+1]).read_text().strip()
evidence={'url':url,'title':'Tutor','text':'I tutor maths in Amersham. '*15,'links':[]}
result={'node':{'url':url},'evidence':evidence}
if 'broken' in url: result={'node':{'url':url},'error':'HTTP 500'}
checkpoint.write_text(json.dumps({'results':[result],'pages':1}))
''')
        self.binary.chmod(0o700)
        self.ledger = self.root / 'paid.sqlite'
        with closing(sqlite3.connect(self.ledger)) as db, db:
            db.executescript('CREATE TABLE settings(id INTEGER,prior INTEGER); INSERT INTO settings VALUES(1,6635); CREATE TABLE requests(cost_cents INTEGER,status TEXT,reservation INTEGER,imported INTEGER);')
        self.policy = Policy(['Amersham'])
        self.frontier = Frontier(self.stage / 'frontier.sqlite', self.policy)
        self.addCleanup(self.frontier.close)
        self.cache = DecisionsCache(self.root / 'decisions.sqlite', ['Amersham'], transport=lambda _: response())
        self.addCleanup(self.cache.close)
        self.job = {'policy': self.policy.__dict__, 'cache': str(self.cache.path),
                    'paidLedger': str(self.ledger), 'priorExposureCents': 6635,
                    'crawler': str(self.binary), 'crawlerSha256': file_hash(self.binary),
                    'classificationCapNano': 3_000_000_000, 'sourceSha': 'fixture',
                    'deadlineUnix': time.time() + 300}
        atomic_json(self.stage / 'job.json', self.job)
        atomic_json(self.stage / 'status.json', {'status': 'prepared'})
        focused_run.STOP = False

    def run_stage(self):
        with patch('focused_run.DecisionsCache', return_value=self.cache), patch('focused_run.time.sleep'):
            # Keep the shared assertion handle open after run's normal dispose.
            with patch.object(self.cache, 'close'):
                focused_run.run(self.stage)

    def test_new_upstream_batch_is_classified_from_saved_text_without_another_fetch(self):
        import hashlib
        url = 'https://fresh-tutor.co.uk/'
        text = 'I am a private maths tutor in Amersham. ' * 8
        rows = [{'url': url, 'context': {'title': 'Private tutor Amersham'}, 'group': 'new-search',
                 'provenance': {'receiptSha256': 'saved-receipt'},
                 'evidence': {'url': url, 'text': text, 'links': [],
                              'textSha256': hashlib.sha256(text.encode()).hexdigest()}}]
        raw = (json.dumps({'entries': rows}, indent=2, ensure_ascii=False) + '\n').encode()
        atomic_json(self.stage / 'seed-batches' / (hashlib.sha256(raw).hexdigest() + '.json'), {'entries': rows})
        self.run_stage()
        results = json.loads((self.stage / 'research-candidates.json').read_text())
        self.assertEqual([row['url'] for row in results], [url])
        self.assertFalse((self.stage / 'fetches').exists())
        self.assertEqual(json.loads((self.stage / 'job.json').read_text())['deadlineUnix'], self.job['deadlineUnix'])

    @patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-never-pass-to-crawler'})
    def test_failed_page_does_not_stop_next_page_and_final_artifacts_survive_cleanup(self):
        self.frontier.add('https://broken.co.uk/', {'title': 'Tutor in Amersham'})
        self.frontier.add('https://good.co.uk/', {'title': 'Tutor in Amersham'})
        self.run_stage()
        status = json.loads((self.stage / 'status.json').read_text())
        self.assertEqual(status['status'], 'complete')
        self.assertEqual(status['states'], {'classified': 1, 'error': 1})
        self.assertEqual(status['localTutorDomains'], 1)
        self.assertEqual(len(json.loads((self.stage / 'research-candidates.json').read_text())), 1)
        self.assertTrue((self.stage / 'frontier.sqlite').exists())
        self.assertFalse(list((self.stage / 'fetches').iterdir()))
        self.assertLess(status['sharedExposureUsd'], 66.36)

    def test_finished_stage_cannot_restart_or_repay(self):
        self.frontier.add('https://good.co.uk/', {'title': 'Tutor in Amersham'})
        self.run_stage()
        before = self.cache.summary()
        self.run_stage()
        self.assertEqual(before, self.cache.summary())

    def test_expired_deadline_finishes_without_fetch_or_api(self):
        self.frontier.add('https://good.co.uk/', {'title': 'Tutor in Amersham'})
        self.job['deadlineUnix'] = time.time() - 1
        atomic_json(self.stage / 'job.json', self.job)
        self.run_stage()
        self.assertEqual(self.frontier.summary()['attempts'], 0)
        self.assertEqual(self.cache.summary()['spentUsd'], 0)

    def test_original_budget_drift_fails_before_any_work(self):
        with closing(sqlite3.connect(self.ledger)) as db, db: db.execute('UPDATE settings SET prior=6636')
        with self.assertRaises(ValueError): self.run_stage()
        self.assertEqual(self.cache.summary()['states'], {})

    def test_saved_evidence_never_fetches_again(self):
        evidence = {'url': 'https://good.co.uk/', 'text': 'I tutor in Amersham. ' * 10, 'links': []}
        self.frontier.add(evidence['url'], {'title': 'Tutor'}, evidence=evidence)
        self.run_stage()
        self.assertFalse((self.stage / 'fetches').exists())

    def test_directory_uses_existing_go_research_fetch_and_retains_text_hash(self):
        self.frontier.add('https://tutorperch.com/t/jane-smith', {'title': 'Tutor in Amersham'})
        self.run_stage()
        result = json.loads((self.stage / 'research-candidates.json').read_text())[0]
        self.assertEqual(len(result['textSha256']), 64)

    def test_fetches_overlap_on_different_hosts(self):
        self.job['fetchWorkers'] = 2
        atomic_json(self.stage / 'job.json', self.job)
        for host in ('first.co.uk', 'second.co.uk'):
            self.frontier.add('https://' + host + '/', {'title': 'Tutor in Amersham'})
        barrier = threading.Barrier(2)
        def overlapping_fetch(job, root, row):
            barrier.wait(timeout=3)
            return {'url': row['url'], 'text': 'I tutor in Amersham. ' * 15, 'links': []}
        with patch('focused_run.fetch', side_effect=overlapping_fetch):
            self.run_stage()
        status = json.loads((self.stage / 'status.json').read_text())
        self.assertEqual(status['states'], {'classified': 2})

    def test_continuation_preserves_lineage_decisions_errors_and_old_deadline(self):
        self.frontier.add('https://good.co.uk/', {'title': 'Tutor in Amersham'})
        self.frontier.add('https://broken.co.uk/', {'title': 'Tutor in Amersham'})
        self.run_stage()
        old_job = (self.stage / 'job.json').read_bytes()
        new_stage = self.root / 'continuation'
        new_stage.mkdir()
        policy = Policy(['Amersham'], max_pages=10000, directory_per_site=4000)
        copied = focused_run.carry_frontier(new_stage, self.stage, policy, self.cache.path)
        continued = Frontier(new_stage / 'frontier.sqlite', policy)
        try:
            self.assertEqual(continued.summary()['attempts'], 2)
            self.assertEqual(continued.summary()['states'], {'classified': 1, 'error': 1})
            self.assertEqual(copied['initialAttempts'], 2)
            self.assertEqual((self.stage / 'job.json').read_bytes(), old_job)
            self.assertIsNotNone(continued.db.execute('SELECT decision FROM pages WHERE state=\'classified\'').fetchone()[0])
        finally:
            continued.close()

    def test_active_stage_cannot_be_cloned(self):
        new_stage = self.root / 'continuation'
        new_stage.mkdir()
        with self.assertRaises(ValueError):
            focused_run.carry_frontier(new_stage, self.stage, self.policy, self.cache.path)

    def test_api_failure_stops_submissions_and_keeps_other_fetched_evidence(self):
        self.job['fetchWorkers'] = 2
        atomic_json(self.stage / 'job.json', self.job)
        for host in ('first.co.uk', 'second.co.uk'):
            self.frontier.add('https://' + host + '/', {'title': 'Tutor in Amersham'})
        with patch.object(self.cache, 'classify', side_effect=APIUnavailable('API unavailable')) as classify:
            self.run_stage()
        self.assertEqual(classify.call_count, 1)
        status = json.loads((self.stage / 'status.json').read_text())
        self.assertEqual(status['status'], 'api_unavailable')
        self.assertEqual(status['states'], {'held': 2})
        self.assertEqual(self.frontier.db.execute('SELECT count(*) FROM pages WHERE evidence IS NOT NULL').fetchone()[0], 2)

    def test_slow_host_does_not_block_next_page_on_a_fast_host(self):
        self.job['fetchWorkers'] = 2
        atomic_json(self.stage / 'job.json', self.job)
        for url in ('https://slow.co.uk/', 'https://fast.co.uk/about', 'https://fast.co.uk/contact'):
            self.frontier.add(url, {'title': 'Tutor in Amersham'})
        fast_finished = threading.Event()
        fast_calls = []
        def fetch(job, root, row):
            if row['site'] == 'slow.co.uk':
                if not fast_finished.wait(timeout=5):
                    raise ValueError('Slow host blocked the second fast page')
            else:
                fast_calls.append(row['url'])
                if len(fast_calls) == 2:
                    fast_finished.set()
            return {'url': row['url'], 'text': 'I tutor in Amersham. ' * 15, 'links': []}
        with patch('focused_run.fetch', side_effect=fetch):
            self.run_stage()
        self.assertEqual(json.loads((self.stage / 'status.json').read_text())['states'], {'classified': 3})


if __name__ == '__main__': unittest.main()
