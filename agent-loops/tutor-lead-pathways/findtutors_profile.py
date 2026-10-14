"""Read a FindTutors person's public profile, excluding recommendations."""
import re
from urllib.parse import urlsplit


def advert_person_id(document, url):
    from directory_profiles import admitted_url
    match = re.fullmatch(r'/(?:tutors-[^/]+|online-[^/]+)/[^/]+-(\d+)/?', urlsplit(url).path)
    canonical = [n.attrs.get('content') for n in document.walk()
                 if n.tag == 'meta' and n.attrs.get('property') == 'og:url']
    sections = [n for n in document.walk() if n.attrs.get('id') == 'sectionaddetail']
    adverts = [n.attrs.get('value') for n in document.walk() if n.attrs.get('id') == 'idad']
    people = [n.attrs.get('value') for n in document.walk() if n.attrs.get('id') == 'iduser']
    reports = [n.attrs.get('data-u') for n in document.walk() if n.attrs.get('id') == 'report_error']
    if (not match or len(canonical) != 1 or admitted_url(canonical[0]) != url or len(sections) != 1
            or adverts != [match[1]] or len(people) != 1 or not re.fullmatch(r'[1-9]\d{0,15}', people[0] or '')
            or reports != people):
        return None
    return people[0]


def parse_profile(document, url):
    from directory_profiles import admitted_url, annotate_advert, locality_status, public_person
    users = [n for n in document.walk() if n.attrs.get('id') == 'user-data']
    recipients = [n for n in document.walk() if n.attrs.get('id') == 'contact_user_id']
    canonical = [n.attrs.get('content') for n in document.walk()
                 if n.tag == 'meta' and n.attrs.get('property') == 'og:url']
    if len(users) != 1 or len(recipients) != 1 or len(canonical) != 1 or admitted_url(canonical[0]) != url:
        return None
    person = recipients[0].attrs.get('value', '')
    names = [n.text() for n in users[0].walk() if n.tag == 'h1']
    classes = [n for n in users[0].walk() if n.attrs.get('id') == 'user_clasesde']
    if (not re.fullmatch(r'[1-9]\d{0,15}', person) or len(names) != 1 or not names[0]
            or len(classes) != 1 or not document.first(lambda n: n.tag == 'h2' and n.text() == 'Profile of ' + names[0])):
        return None
    name = names[0]
    subjects = [s.strip(' ,') for s in re.split(r'\bclasses of\s+', classes[0].text(), flags=re.I)[1:]]
    descriptions = [n.attrs.get('content', '') for n in document.walk()
                    if n.tag == 'meta' and n.attrs.get('property') == 'og:description']
    if len(descriptions) != 1:
        return None
    description = descriptions[0]
    own = re.fullmatch(re.escape(name) + r', private teacher from (.+?) offers (.+?) classes', description)
    online = re.fullmatch(re.escape(name) + r', online tutor who teaches (.+?) online classes via the internet', description)
    if not own and not online:
        return None
    subjects.extend([own[2] if own else online[1]])
    places = [own[1]] if own else []
    locations = [n for n in document.walk() if n.attrs.get('id') == 'location_box']
    if len(locations) > 1:
        return None
    if locations:
        places.extend(p.strip() for p in locations[0].text().removeprefix('Location of my classes').split('·') if p.strip())
    # These are explicitly the owner's class locations, not towns copied from
    # breadcrumbs or the "other tutors" section. Online alone is not local.
    local = [p for p in places if locality_status(p) == 'advertised_bucks_locality']
    location = local[0] if local else places[0] if places else 'Online'
    row = public_person('findtutors.co.uk', person, name, location, sorted(set(subjects)), url)
    row['additionalLocalityClaims'] = list(dict.fromkeys(places))
    row['profileRoute'] = url
    return annotate_advert(row)
