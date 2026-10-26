import importlib.util
from pathlib import Path
import unittest

p = Path(__file__).resolve().parents[1] / 'media-quality/video-review/repair_cta_relevance.py'
s = importlib.util.spec_from_file_location('relevance', p)
r = importlib.util.module_from_spec(s)
s.loader.exec_module(r)


class RelevanceTests(unittest.TestCase):
    def test_subject_offers_route_to_existing_matching_endings(self):
        for subject, target in [('english', 'cta-sc-18'), ('maths', 'cta-sc-14'), ('vr', 'cta-sc-06')]:
            self.assertEqual(r.target_cta({'kind': 'teaching', 'subject': subject, 'ctaId': 'cta-sc-05'}), target)
        self.assertEqual(r.target_cta({'kind': 'teaching', 'subject': 'maths', 'ctaId': 'cta-sc-07'}), 'cta-sc-14')

    def test_requested_folding_demonstrations_are_preserved(self):
        for kind, subject in [('cta', None), ('marketing', None), ('teaching', 'nvr')]:
            for cta in ['cta-sc-05', 'cta-sc-07']:
                self.assertIsNone(r.target_cta({'kind': kind, 'subject': subject, 'ctaId': cta}))

    def test_other_endings_and_removed_work_are_untouched(self):
        self.assertIsNone(r.target_cta({'kind': 'teaching', 'subject': 'english', 'ctaId': 'cta-sc-18'}))
        self.assertIsNone(r.target_cta({'kind': 'teaching', 'subject': 'english', 'ctaId': 'cta-sc-05', 'removedFromRelease': True}))

    def test_all_subject_offer_does_not_survive_subject_landing_page(self):
        for subject in ['english', 'maths', 'nvr', 'vr']:
            self.assertIsNotNone(r.target_cta({'kind': 'teaching', 'subject': subject, 'ctaId': 'cta-sc-15'}))
        self.assertIsNone(r.target_cta({'kind': 'marketing', 'subject': None, 'ctaId': 'cta-sc-15'}))


if __name__ == '__main__':
    unittest.main()
