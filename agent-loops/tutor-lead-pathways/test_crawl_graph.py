import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest


spec = importlib.util.spec_from_file_location('crawl_graph', Path(__file__).with_name('crawl_graph.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
DomainGraph = module.DomainGraph


class DomainGraphTests(unittest.TestCase):
    def test_only_observed_cross_domain_relationships_are_edges(self):
        graph = DomainGraph()
        graph.checkpoint({'results': [
            {'node': {'url': 'https://a.test/page', 'root': 'https://seed.test/'},
             'evidence': {'url': 'https://a.test/page', 'text': 'Tutor', 'links': [
                 {'url': 'https://www.b.test/contact', 'anchor': 'A partner'},
                 {'url': 'https://a.test/other'}, {'url': 'javascript:alert(1)'}]},
             'fetchedAt': '2026-10-17T10:00:00Z'},
            {'node': {'url': 'https://isolated.test/'}, 'evidence': {'text': 'Hello'},
             'fetchedAt': '2026-10-17T10:00:00Z'}]}, 'crawl.json', 'run')
        self.assertEqual(set(graph.edges), {('a.test', 'b.test', 'hyperlink')})
        self.assertNotIn('seed.test', graph.nodes)
        self.assertEqual(graph.edges[('a.test', 'b.test', 'hyperlink')]['evidence'][0]['anchor'], 'A partner')

    def test_copied_receipts_do_not_double_count_observations(self):
        graph = DomainGraph()
        for artifact in ['old/receipt.json', 'new/receipt.json']:
            graph.observation('https://a.test/', '2026-10-17T10:00:00Z', artifact, 'run', 'Go crawler',
                              text=True, content_hash='abc', links=[{'url': 'https://b.test/'}])
        result = graph.result({})
        self.assertEqual(result['meta']['fetchedPages'], 1)
        self.assertEqual(result['meta']['observationsBySource'], {'Go crawler': 1})
        self.assertEqual(result['edges'][0]['weight'], 1)

    def test_failed_observations_remain_distinct_from_success(self):
        graph = DomainGraph()
        graph.observation('https://a.test/', '2026-10-17T10:00:00Z', 'a', 'run', 'Go crawler', error='HTTP 503')
        graph.observation('https://a.test/', '2026-10-17T11:00:00Z', 'b', 'run', 'Go crawler', text=True)
        row = graph.result({})['nodes'][0]
        self.assertEqual((row['fetchedPages'], row['textPages'], row['heldPages']), (1, 1, 1))
        self.assertEqual(row['category'], 'crawled')

    def test_profile_evidence_does_not_grant_outreach_or_create_unobserved_domains(self):
        graph = DomainGraph()
        graph.node('https://directory.test/tutors/a')
        graph.attach_profiles([{'canonicalId': 'tutor-1', 'name': 'Tutor One',
            'website': 'https://unobserved.test/', 'sourceUrls': ['https://directory.test/tutors/a'],
            'decision': {'decisionKey': 'sha256:abc', 'kind': 'independent_tutor'},
            'status': 'model_qualified_research', 'outreachEligible': True}])
        row = graph.result({})['nodes'][0]
        self.assertEqual(list(graph.nodes), ['directory.test'])
        self.assertEqual(row['category'], 'research')
        self.assertEqual(row['profiles'][0]['decisionKey'], 'sha256:abc')
        self.assertFalse(row['profiles'][0]['outreachEligible'])

    def test_frontier_queue_is_not_a_successful_fetch(self):
        with tempfile.TemporaryDirectory() as temp:
            runtime = Path(temp)
            qualification = runtime / 'state/qualification'
            qualification.mkdir(parents=True)
            dbpath = runtime / 'frontier.sqlite'
            with sqlite3.connect(dbpath) as db:
                db.execute('CREATE TABLE pages(key PRIMARY KEY,url,parent,evidence,state,reason,created_at)')
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)',
                    ('queued-sha', 'https://queued.test/', 'parent-sha', None, 'pending', None, '2026-10-17T10:00:00Z'))
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)',
                    ('parent-sha', 'https://parent.test/', None, None, 'pending', None, '2026-10-17T10:00:00Z'))
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)',
                    ('fetched-sha', 'https://fetched.test/', None, json.dumps({'text': 'Tutor'}), 'fetched', None, '2026-10-17T11:00:00Z'))
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)',
                    ('error-sha', 'https://error.test/', 'https://queued.test/', None, 'error', 'Timeout', '2026-10-17T11:00:00Z'))
                db.execute('INSERT INTO pages VALUES(?,?,?,?,?,?,?)',
                    ('held-sha', 'https://classification-held.test/', None,
                     json.dumps({'text': 'Saved tutor context'}), 'held', 'Decisions hold', '2026-10-17T11:00:00Z'))
            (qualification / 'job.json').write_text(json.dumps({'frontiers': [str(dbpath)]}))
            output = runtime / 'graph.json'
            module.export(runtime, qualification, output)
            data = json.loads(output.read_text())
            rows = {n['id']: n for n in data['nodes']}
            self.assertEqual(rows['queued.test']['category'], 'discovered')
            self.assertEqual(rows['queued.test']['fetchedPages'], 0)
            self.assertEqual(rows['fetched.test']['fetchedPages'], 1)
            self.assertEqual(rows['error.test']['category'], 'held')
            self.assertEqual(rows['error.test']['fetchedPages'], 0)
            self.assertEqual(rows['classification-held.test']['fetchedPages'], 1)
            self.assertEqual(rows['classification-held.test']['heldPages'], 0)
            self.assertEqual({(e['source'], e['target'], e['kind']) for e in data['edges']},
                             {('parent.test', 'queued.test', 'crawl_path'), ('queued.test', 'error.test', 'crawl_path')})
            self.assertEqual(data['meta']['domainsFetched'], 2)

    def test_url_boundaries_and_timestamp_normalisation(self):
        for bad in ['javascript:alert(1)', 'https://user:pass@a.test/', 'http://localhost/', 'file:///tmp/a', None]:
            self.assertIsNone(module.public_url(bad))
        self.assertEqual(module.domain('https://WWW.a.test/p#x'), 'a.test')
        self.assertEqual(module.domain('https://sub.a.test/'), 'sub.a.test')
        self.assertEqual(module.timestamp('2026-10-17T11:00:00+01:00'), '2026-10-17T10:00:00+00:00')


if __name__ == '__main__':
    unittest.main()
