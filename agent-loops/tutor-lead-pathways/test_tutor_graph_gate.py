from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location('tutor_graph_gate', Path(__file__).with_name('tutor_graph_gate.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.profile = {'canonicalId': 'person-1', 'name': 'Tutor', 'website': 'https://tutor.test/',
            'localityClaim': 'Milton Keynes', 'matchedSubjects': ['maths'], 'status': 'model_qualified_research',
            'pageEvidence': [{'url': 'https://tutor.test/about', 'textSha256': 'a' * 64}],
            'decision': {'kind': 'independent_tutor', 'kindProbability': .9, 'niche': .95, 'local': .95,
                         'outside': .01, 'decisionKey': 'saved-decision'}}
        self.node = {'id': 'tutor.test', 'category': 'crawled', 'fetchedPages': 2, 'textPages': 2,
                     'profiles': [], 'inDegree': 1, 'outDegree': 1}
        self.graph = {'schemaVersion': 1, 'meta': {}, 'nodes': [self.node,
            dict(self.node, id='directory.test')], 'edges': [
                {'source': 'directory.test', 'target': 'tutor.test', 'kind': 'hyperlink'}]}

    def test_only_tutor_site_and_observed_edges_survive(self):
        result, audit = module.build(self.graph, [self.profile])
        self.assertEqual([n['id'] for n in result['nodes']], ['tutor.test'])
        self.assertEqual(result['edges'], [])
        self.assertTrue(result['meta']['tutorOnly'])
        self.assertFalse(result['nodes'][0]['profiles'][0]['outreachEligible'])
        self.assertEqual(audit['reclassificationCalls'], 0)

    def test_directory_profile_is_not_its_own_tutor_domain(self):
        for host in ['tutorhunt.com', 'wendover.universitytutor.com', 'thetutorsassociation.wildapricot.org',
                     'school.bucks.sch.uk', 'buckinghamshire.gov.uk']:
            profile = deepcopy(self.profile)
            profile['website'] = 'https://' + host + '/profile'
            profile['pageEvidence'][0]['url'] = profile['website']
            self.assertEqual(module.admission(profile, {host: self.node})[1], 'not_tutor_owned_hostname')

    def test_fail_closed_for_subject_locality_kind_or_context(self):
        changes = [('local', .4), ('niche', .4), ('kindProbability', .7), ('outside', .9),
                   ('kind', 'directory'), ('kindProbability', float('nan'))]
        for key, value in changes:
            profile = deepcopy(self.profile)
            profile['decision'][key] = value
            self.assertIsNone(module.admission(profile, {'tutor.test': self.node})[0])
        profile = deepcopy(self.profile)
        profile['matchedSubjects'] = ['music']
        self.assertEqual(module.admission(profile, {'tutor.test': self.node})[1], 'missing_local_subject_evidence')
        profile = deepcopy(self.profile)
        profile['pageEvidence'][0]['url'] = 'https://directory.test/other-person'
        self.assertEqual(module.admission(profile, {'tutor.test': self.node})[1], 'missing_same_host_page_evidence')

    def test_duplicates_and_repeat_updates_do_not_inflate_growth(self):
        first, _ = module.build(self.graph, [self.profile, self.profile])
        second, audit = module.build(self.graph, [self.profile], first)
        self.assertEqual(second['meta']['modelQualifiedProfiles'], 1)
        self.assertEqual(audit['newHostnames'], [])
        self.assertEqual(audit['removedHostnames'], [])


if __name__ == '__main__':
    unittest.main()
