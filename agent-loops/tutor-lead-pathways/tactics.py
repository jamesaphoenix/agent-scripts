"""New county discovery queries, retaining semantic paid-request identities."""
from discovery import request_key
from keyword_expansion import keyword_plan

PROFILE_SITES = (
    'superprof.co.uk', 'firsttutors.com', 'tutorhunt.com', 'tutorful.co.uk',
    'tutorextra.co.uk', 'uktutors.com', 'tutordex.net', 'childcare.co.uk',
    'tutorperch.com', 'universitytutor.com', 'findtutors.co.uk',
)


def build_plan(towns):
    result = keyword_plan(towns)
    seen = {item['key'] for item in result}
    def add(lane, tactic, town, payload):
        key = request_key(lane, payload)
        if key not in seen:
            seen.add(key)
            result.append({'lane': lane, 'tactic': tactic, 'town': town['name'],
                           'key': key, 'payload': payload})
    def organic(lane, tactic, town, query, depth=30):
        add(lane, tactic, town, {'keyword': query, 'location_coordinate': town['coordinate'] + ',200',
            'language_code': 'en', 'se_domain': 'google.co.uk', 'depth': depth})
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


def campaign_plan(towns):
    """Fresh source families and subject variants, each still bounded to a town."""
    result = build_plan(towns)
    for item in result:
        if item.get('tactic') == 'public_profiles':
            site = item['payload']['keyword'].split()[0].removeprefix('site:')
            item['tactic'] += ':' + site
    seen = {item['key'] for item in result}
    def add(lane, tactic, town, payload):
        key = request_key(lane, payload)
        if key not in seen:
            seen.add(key)
            result.append({'lane': lane, 'tactic': tactic, 'town': town['name'], 'key': key, 'payload': payload})
    variants = {
        'subject_profiles': [
            'site:{site} "{town}" ("11 plus" OR "eleven plus" OR "entrance exam")',
            'site:{site} "{town}" ("maths" OR "English" OR "science")',
            'site:{site} "{town}" ("GCSE" OR "A level" OR "primary" OR "SEN")',
        ],
        'professional_registers': [
            '"{town}" tutor (site:tutorsassociation.org.uk OR site:thetutorsassociation.org.uk OR site:qualifiedtutor.org)',
            '"{town}" tuition ("independent tutor" OR "private teacher") -kumon -jobs -franchise',
        ],
        'local_adverts': [
            '"{town}" "tutor" ("contact" OR "email") (inurl:newsletter OR filetype:pdf)',
            '"{town}" "tuition" ("parish" OR "community centre" OR "village hall")',
            '"{town}" "tutor" (site:freeindex.co.uk OR site:yell.com OR site:thomsonlocal.com)',
        ],
        'specialist_teachers': [
            '"{town}" ("dyslexia tutor" OR "SEN tutor" OR "special needs tutor")',
            '"{town}" ("verbal reasoning" OR "non verbal reasoning") tutor',
            '"{town}" ("French tutor" OR "Spanish tutor" OR "physics tutor" OR "chemistry tutor") -jobs',
        ],
    }
    for tactic, queries in variants.items():
        sites = ('tutorhunt.com', 'superprof.co.uk', 'tutorful.co.uk', 'findtutors.co.uk', 'uktutors.com', 'tutorextra.co.uk') if tactic == 'subject_profiles' else (None,)
        for query in queries:
            for site in sites:
                for town in towns:
                    add('directories' if site else 'organic', tactic + (':' + site if site else ''), town,
                        {'keyword': query.format(town=town['name'], site=site),
                         'location_coordinate': town['coordinate'] + ',200', 'language_code': 'en',
                         'se_domain': 'google.co.uk', 'depth': 50 if site else 30})
    for subject in ('private tuition', 'entrance exam tutor', '11%2B tuition', 'SEN tutor',
                    'English teacher', 'mathematics tutor', 'home tuition'):
        for town in towns:
            add('maps', 'maps_neighbourhood', town,
                {'keyword': f'{subject} {town["name"]}', 'location_coordinate': town['coordinate'] + ',15z',
                 'language_code': 'en', 'se_domain': 'google.co.uk', 'depth': 100,
                 'search_places': False, 'search_this_area': True})
    for town in towns:
        # A single 1000-row response costs at most USD0.372 at the published
        # USD0.012 task + USD0.00036/item rate. The USD1 reservation covers it.
        add('business', 'business_county_categories', town,
            {'categories': ['tutoring_service', 'private_tutor'], 'location_coordinate': town['coordinate'] + ',8',
             'limit': 1000, 'offset': 0})
    # New source families precede exhausted legacy variants. The runner explores
    # groups, then uses measured novelty to order subsequent queries.
    return sorted(result, key=lambda item: 0 if item.get('tactic') in ('business_county_categories', 'maps_neighbourhood', 'specialist_teachers', 'local_adverts', 'professional_registers') or item.get('tactic', '').startswith('subject_profiles:') else 1)
