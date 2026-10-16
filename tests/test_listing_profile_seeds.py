import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'agent-loops/tutor-lead-pathways'))
from directory_profiles import exclusion_reason, listing_profile_urls


class ListingProfileSeedsTests(unittest.TestCase):
    def card(self, **changes):
        return dict({'name': 'Sky Rogers', 'platform': 'tutorextra.co.uk',
            'localityClaim': 'High Wycombe', 'services': ['Tutor, Teacher'],
            'sourceUrls': ['https://tutorextra.co.uk/profile/experienced-teacher/50-64971'],
            'evidenceKind': 'directory_listing', 'outreachEligible': False}, **changes)

    def test_local_tutor_role_can_reach_its_profile_for_subject_review(self):
        row = self.card()
        self.assertEqual(listing_profile_urls([row], 'tutorextra.co.uk'), row['sourceUrls'])
        self.assertEqual(exclusion_reason(row), 'needs_subject_review')
        self.assertFalse(row['outreachEligible'])

    def test_outside_and_unknown_locations_do_not_enter_the_profile_queue(self):
        for town in ['London', 'Reading', 'Online', '']:
            with self.subTest(town=town):
                self.assertEqual(listing_profile_urls([self.card(localityClaim=town)], 'tutorextra.co.uk'), [])

    def test_other_exclusions_are_preserved_before_fetching(self):
        for changes in [{'institution': True}, {'advertisedUnavailable': True},
                        {'nonTuitionAdvert': True}, {'hasQualifyingTutorAdvert': False},
                        {'services': ['Yoga Teacher']}]:
            with self.subTest(changes=changes):
                self.assertEqual(listing_profile_urls([self.card(**changes)], 'tutorextra.co.uk'), [])

    def test_deferred_subject_review_requires_the_expected_listing_and_tutor_role(self):
        for changes in [{'evidenceKind': 'public_person_profile'},
                        {'platform': 'elsewhere.example'}, {'services': []},
                        {'services': ['Instructor']}]:
            with self.subTest(changes=changes):
                self.assertEqual(listing_profile_urls([self.card(**changes)], 'tutorextra.co.uk'), [])

    def test_other_sources_keep_the_existing_subject_requirement(self):
        row = self.card(platform='findtutors.co.uk')
        self.assertEqual(listing_profile_urls([row], 'www.findtutors.co.uk'), [])

    def test_subject_specific_local_listing_still_enters_the_queue(self):
        row = self.card(services=['Chemistry Tutor'])
        self.assertEqual(listing_profile_urls([row], 'tutorextra.co.uk'), row['sourceUrls'])

    def test_childcare_listing_keeps_its_existing_profile_locality_review(self):
        row = self.card(platform='childcare.co.uk', services=[], localityClaim='')
        self.assertEqual(listing_profile_urls([row], 'www.childcare.co.uk'), row['sourceUrls'])

    def test_a_profile_with_only_a_role_still_cannot_qualify_as_a_contact(self):
        row = self.card(evidenceKind='public_person_profile')
        self.assertEqual(exclusion_reason(row), 'needs_subject_review')
        self.assertEqual(listing_profile_urls([row], 'tutorextra.co.uk'), [])


if __name__ == '__main__':
    unittest.main()
