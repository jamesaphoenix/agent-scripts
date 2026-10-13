"""Fresh sources reach the single-writer classifier without buying or fetching twice."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from classifier_seeds import ingest, publish
from discovery import PaidCache, atomic_json
from focused_frontier import Frontier, Policy


class ClassifierSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'paid'
        self.stage = Path(self.tmp.name) / 'focused'
        self.stage.mkdir()
        self.cache = PaidCache(self.root, 3065, 6935)
        self.addCleanup(self.cache.db.close)
        self.frontier = Frontier(self.stage / 'frontier.sqlite', Policy(['Amersham']))
        self.addCleanup(self.frontier.close)

    def receipt(self, url, inherited=False):
        response = {'status_code': 20000, 'cost': .01,
                    'tasks': [{'id': url, 'status_code': 20000, 'cost': .01, 'result': [{'items': [{'url': url}]}]}]}
        payload = {'keyword': 'private tutor Amersham ' + url}
        if inherited:
            self.cache.import_receipt('organic', payload, response)
        else:
            from discovery import request_key
            key = request_key('organic', payload)
            self.cache.reserve(key, 'organic', payload, 100)
            self.cache.settle(key, response)

    def batch(self, rows):
        raw = (json.dumps({'entries': rows}, indent=2, ensure_ascii=False) + '\n').encode()
        path = self.stage / 'seed-batches' / (hashlib.sha256(raw).hexdigest() + '.json')
        atomic_json(path, {'entries': rows})
        return path

    def test_cached_text_and_new_provider_results_are_reused_without_paid_calls(self):
        url = 'https://individual.co.uk/'
        self.receipt(url)
        self.receipt('https://old.co.uk/', inherited=True)
        text = 'Zoë teaches maths in Amersham. ' * 5
        atomic_json(self.root / 'crawls/one/crawl.json', {'results': [
            {'node': {'url': url}, 'evidence': {'url': url, 'text': text, 'links': []}}]})
        before = self.cache.usage()
        with patch('discovery.urllib.request.build_opener') as network:
            report = publish(self.root, [self.stage], self.cache)
            self.assertEqual(report['urls'], 1)
            self.assertEqual(ingest(self.stage, self.frontier), 1)
            self.assertEqual(ingest(self.stage, self.frontier), 0)
            network.assert_not_called()
        self.assertEqual(before, self.cache.usage())
        row = self.frontier.claim()
        self.assertEqual(json.loads(row['evidence'])['text'], text)
        self.assertEqual(row['state'], 'fetching')
        self.assertEqual(row['depth'], 0)
        self.assertEqual(row['group_key'], self.cache.db.execute("SELECT key FROM requests WHERE imported=0").fetchone()[0])

    def test_tampered_batch_is_held_without_stopping_a_valid_batch(self):
        row = {'url': 'https://goodtutor.co.uk/', 'context': {'title': 'Tutor'}, 'group': 'fresh', 'provenance': {}}
        invalid = self.batch([dict(row, url='https://badtutor.co.uk/')])
        invalid.write_text(invalid.read_text().replace('badtutor', 'changed'))
        self.batch([row])
        self.assertEqual(ingest(self.stage, self.frontier), 1)
        statuses = json.loads((self.stage / 'seed-ingestion.json').read_text())['processed']
        self.assertEqual(sorted(v['status'] for v in statuses.values()), ['held', 'ingested'])

    def test_evidence_hash_failure_does_not_partially_import_the_batch(self):
        row = {'url': 'https://goodtutor.co.uk/', 'context': {}, 'group': 'fresh', 'provenance': {}}
        self.batch([row, dict(row, url='https://corrupt.co.uk/', evidence={'text': 'x' * 100, 'textSha256': 'wrong'})])
        self.assertEqual(ingest(self.stage, self.frontier), 0)
        self.assertEqual(self.frontier.summary()['pages'], 0)

    def test_fresh_upstream_result_keeps_existing_recursive_lineage_and_limits(self):
        self.frontier.add('https://tutor.co.uk/', {})
        parent = self.frontier.claim()
        url = 'https://childtutor.co.uk/'
        self.frontier.add(url, {}, parent=parent)
        self.receipt(url)
        publish(self.root, [self.stage], self.cache)
        self.assertEqual(ingest(self.stage, self.frontier), 0)
        child = self.frontier.db.execute('SELECT depth,parent FROM pages WHERE url=?', (url,)).fetchone()
        self.assertEqual(tuple(child), (1, parent['key']))
        self.assertEqual(self.frontier.policy.branch_sites, 15)

    def test_batch_failure_rolls_back_all_insertions(self):
        row = {'url': 'https://goodtutor.co.uk/', 'context': {}, 'group': 'fresh', 'provenance': {}}
        invalid = dict(row, url='https://broken.co.uk/', provenance={'invalid': object()})
        with self.assertRaises(TypeError):
            self.frontier.add_many([row, invalid])
        self.assertEqual(self.frontier.summary()['pages'], 0)


if __name__ == '__main__':
    unittest.main()
