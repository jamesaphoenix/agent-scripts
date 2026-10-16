"""Read public University Tutor profiles within the approved local towns."""
import re
from urllib.parse import urlsplit

HOSTS = {town + '.universitytutor.com' for town in (
    'wendover', 'aylesbury', 'highwycombe', 'miltonkeynes',
    'amersham', 'chesham', 'marlow', 'buckingham')}
PROFILE = re.compile(r'/tutors/([1-9]\d{0,11})')


def listing_page(url):
    parsed = urlsplit(url)
    slug = parsed.hostname.split('.')[0] if parsed.hostname in HOSTS else ''
    match = re.fullmatch('/' + re.escape(slug) + r'_(tutoring|(?:english|math)-tutoring)(?:/([2-9]|[12]\d|30))?', parsed.path)
    return (match[1], int(match[2] or 1)) if slug and match else None


def public_path(url):
    parsed = urlsplit(url)
    return bool(parsed.hostname in HOSTS and not parsed.query and (
        parsed.path == '/robots.txt' or PROFILE.fullmatch(parsed.path) or listing_page(url)))


def local_link(value, source):
    target, origin = urlsplit(value), urlsplit(source)
    if not public_path(value) or origin.hostname not in HOSTS:
        return False
    if PROFILE.fullmatch(target.path):
        return True
    a, b = listing_page(value), listing_page(source)
    return bool(target.hostname == origin.hostname and a and b and a[0] == b[0] and a[1] == b[1] + 1)


def bounded_frontier(links):
    profiles = [url for url in links if PROFILE.fullmatch(urlsplit(url).path)]
    pagination = [url for url in links if listing_page(url)]
    return list(dict.fromkeys(profiles))[:15] + list(dict.fromkeys(pagination))[:1]


def parse_profile(document, url):
    from directory_profiles import annotate_advert, parent_node, public_person
    parsed = urlsplit(url)
    match = PROFILE.fullmatch(parsed.path)
    if not public_path(url) or not match:
        return None
    # A directory heading or neighbouring card never supplies these fields.
    roles = [n for n in document.walk() if n.tag == 'p'
             and re.fullmatch(r'Private tutor in (.+), United Kingdom', n.text())]
    if len(roles) != 1:
        return None
    header = parent_node(document, roles[0])
    names = [n for n in header.children if getattr(n, 'tag', None) == 'h3']
    if len(names) != 1 or not names[0].text() or len(names[0].text()) > 120:
        return None
    name = names[0].text()
    place = re.fullmatch(r'Private tutor in (.+), United Kingdom', roles[0].text())[1]
    canonicals = [n.attrs.get('href') for n in document.walk()
                  if n.tag == 'link' and n.attrs.get('rel') == 'canonical']
    if canonicals != [url]:
        return None
    owner = header
    while owner and not all(owner.first(lambda n: n.tag == 'h4' and n.text() == label)
                            for label in ('Subject Expertise', 'Experience')):
        owner = parent_node(document, owner)
    if owner is None or [n.text() for n in owner.walk() if n.tag == 'h3'] != [name]:
        return None

    def section(label):
        headings = [n for n in owner.walk() if n.tag == 'h4' and n.text() == label]
        parent = parent_node(document, headings[0]) if len(headings) == 1 else None
        if parent is None or parent.tag != 'section':
            return None
        paragraphs = [n for n in parent.children if getattr(n, 'tag', None) == 'p']
        return paragraphs[0].text() if len(paragraphs) == 1 else None

    subjects, experience = section('Subject Expertise'), section('Experience')
    if not subjects or not experience:
        return None
    services = list(dict.fromkeys(s.strip() for s in subjects.split(',') if s.strip()))
    if not services or len(services) > 80:
        return None
    row = public_person('universitytutor.com', match[1], name, place, services, url)
    return annotate_advert(row, description=experience)
