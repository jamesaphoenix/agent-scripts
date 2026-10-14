"""Read owned public lesson adverts and seed local Apprentus listings."""
import re
from urllib.parse import urljoin, urlsplit

PROFILE_PATH = re.compile(r'/private-lessons/([a-z0-9-]{1,80})/[a-z0-9-]{1,80}/[a-z0-9-]{1,80}/[a-z0-9-]{1,160}')
LISTING_PATH = re.compile(r'/(?:math|english|tutoring)-lessons/([a-z0-9-]{1,80})')
OWNER_PATH = re.compile(r'/in/([a-z0-9][a-z0-9._-]{0,79})')


def bounded_frontier(profile_urls, links):
    return list(dict.fromkeys([*profile_urls, *links]))[:15]


def local_source_path(path):
    from directory_profiles import locality_status
    match = PROFILE_PATH.fullmatch(path) or LISTING_PATH.fullmatch(path)
    return bool(match and locality_status(match[1].replace('-', ' ')) == 'advertised_bucks_locality')


def listing_records(document, url):
    from directory_profiles import admitted_url, public_person
    containers = [n for n in document.walk() if n.attrs.get('id') == 'main-search-results']
    rows, seen = [], set()
    if len(containers) != 1:
        return rows
    for card in containers[0].walk():
        if not card.has_class('search-result'):
            continue
        links = [n for n in card.walk() if n.tag == 'a' and n.has_class('result-content')]
        names = [n for n in card.walk() if n.has_class('name')]
        titles = [n for n in card.walk() if n.has_class('title-desktop')]
        if len(links) != 1 or len(names) != 1 or len(titles) != 1:
            continue
        source = admitted_url(urljoin(url, links[0].attrs.get('href', '')))
        match = PROFILE_PATH.fullmatch(urlsplit(source).path) if source else None
        if not match or not local_source_path(urlsplit(source).path) or source in seen:
            continue
        seen.add(source)
        # The observed URL is only a frontier hint. It never qualifies a person.
        row = public_person('www.apprentus.co.uk', source, names[0].text(), match[1].replace('-', ' '),
                            [titles[0].text().partition('Translate this text')[0].strip()], source)
        rows.append(dict(row, evidenceKind='directory_listing', listingUrl=url,
                         localityEvidence='profile_url_hint_only'))
    return rows


def parse_profile(document, url):
    from directory_profiles import INSTITUTION, annotate_advert, public_person
    if not PROFILE_PATH.fullmatch(urlsplit(url).path):
        return None
    sections = [n for n in document.walk() if n.attrs.get('id') == 'left-section']
    if len(sections) != 1:
        return None
    own = sections[0]
    names = [n for n in own.walk() if n.tag == 'a' and n.has_class('name')]
    titles = [n for n in own.walk() if n.has_class('course-title')]
    descriptions = [n for n in own.walk() if n.has_class('course-description')]
    locations = [n for n in own.walk() if n.has_class('location-container')]
    subjects = list(dict.fromkeys(n.text() for n in own.walk() if n.tag == 'a' and n.has_class('skill-title') and n.text()))
    if any(len(values) != 1 for values in (names, titles, descriptions, locations)) or not subjects:
        return None
    name = names[0].text()
    owner_url = urlsplit(urljoin(url, names[0].attrs.get('href', '')))
    owner = OWNER_PATH.fullmatch(owner_url.path)
    if (not name or len(name) > 160 or not owner or owner_url.hostname != 'www.apprentus.co.uk'
            or owner_url.query or owner_url.fragment):
        return None
    claims = [n.text().removeprefix('Around ').strip() for n in locations[0].walk()
              if n.has_class('location') and n.text()]
    if not claims:
        return None
    places = list(dict.fromkeys(value.split(',', 1)[0].strip() for value in claims))
    countries = [value.rsplit(',', 1)[-1].strip().casefold() for value in claims]
    if re.search(r'\b11\s*\+|\b(?:11|eleven)[ -]+plus\b', titles[0].text(), re.I):
        subjects.append('11+')
    row = public_person('www.apprentus.co.uk', owner[1], name, places[0], subjects, url)
    row.update(evidenceKind='public_advert_person', contactUrl=url,
               contactRouteNote='Public lesson advert. No message or booking is submitted; private profiles are not accessed.',
               ownLessonLocationClaims=claims, locationConflict=len(places) != 1 or any(
                   country not in ('united kingdom', 'uk', 'england', 'great britain') for country in countries),
               institution=bool(INSTITUTION.search(name)))
    return annotate_advert(row, titles[0].text(), descriptions[0].text())
