"""New county discovery queries, retaining semantic paid-request identities."""
from discovery import request_key

PROFILE_SITES = (
    'superprof.co.uk', 'firsttutors.com', 'tutorhunt.com', 'tutorful.co.uk',
    'tutorextra.co.uk', 'uktutors.com', 'tutordex.net', 'childcare.co.uk',
    'tutorperch.com', 'universitytutor.com', 'findtutors.co.uk',
)


def build_plan(towns):
    result, seen = [], set()
    def add(lane, tactic, town, payload):
        key = request_key(lane, payload)
        if key not in seen:
            seen.add(key)
            result.append({'lane': lane, 'tactic': tactic, 'town': town['name'],
                           'key': key, 'payload': payload})
    def organic(lane, tactic, town, query, depth=30):
        add(lane, tactic, town, {'keyword': query, 'location_coordinate': town['coordinate'] + ',200',
            'language_code': 'en', 'se_domain': 'google.co.uk', 'depth': depth})
    # Start with alternative platforms, rather than buying more generic results.
    for site in PROFILE_SITES:
        for town in towns:
            organic('directories', 'public_profiles', town, f'site:{site} "{town["name"]}" tutor')
    for subject in ('maths tutor', 'english tutor', 'science tutor', 'GCSE tutor',
                    'A level tutor', '11%2B tuition', 'home tutor', 'dyslexia tutor'):
        for town in towns:
            add('maps', 'maps_subject', town, {'keyword': f'{subject} {town["name"]} Buckinghamshire',
                'location_coordinate': town['coordinate'] + ',14z', 'language_code': 'en',
                'se_domain': 'google.co.uk', 'depth': 100, 'search_places': False, 'search_this_area': True})
    variants = (
        '"{town}" "tutor" ("I am" OR "my name") -site:findtutors.co.uk -site:superprof.co.uk',
        '"{town}" "tuition" ("qualified teacher" OR "private tutor") -kumon -jobs',
        '"{town}" "11%2B" tutor -kumon -site:findtutors.co.uk',
        '"{town}" "GCSE" "tutor" -site:findtutors.co.uk -site:superprof.co.uk',
        '"{town}" "A level" "tutor" -site:findtutors.co.uk -site:superprof.co.uk',
        '"{town}" "primary" "tuition" -jobs -vacancy',
    )
    for variant in variants:
        for town in towns:
            organic('organic', 'own_sites', town, variant.format(town=town['name']))
    for variant in (
        '"{town}" tutor filetype:pdf',
        '"{town}" tuition (inurl:newsletter OR inurl:parish OR inurl:directory)',
        '"{town}" tutor site:directory.familyinfo.buckinghamshire.gov.uk',
        '"{town}" tutor (site:chilternchatter.com OR site:haddenham.net OR site:redkitedays.co.uk)',
    ):
        for town in towns:
            organic('organic', 'community', town, variant.format(town=town['name']), 20)
    # Unclaimed listings sometimes have no website and are missed by SERP seeds.
    for town in towns:
        add('business', 'unclaimed_listings', town, {'categories': ['tutoring_service', 'private_tutor'],
            'location_coordinate': town['coordinate'] + ',12', 'is_claimed': False, 'limit': 100, 'offset': 0})
    return result
