"""Read public tutor cards and each profile's own declared fields."""
import hashlib
import re
from urllib.parse import urljoin, urlsplit

PROFILE_PATH = re.compile(r'/tutor/[a-z0-9]+(?:-[a-z0-9]+)+/')
LISTING_PATH = re.compile(r'/tutor-list/[a-z0-9]+(?:-[a-z0-9]+)*/')


def listing_records(document, url):
    from directory_profiles import admitted_url, annotate_advert, class_text, public_person
    rows = []
    for card in document.walk():
        if card.tag != 'a' or not card.has_class('trsr1') or not card.has_class('tpt9'):
            continue
        source = admitted_url(urljoin(url, card.attrs.get('href', '')))
        name, place = class_text(card, 'tcn3'), class_text(card, 'tlpr-0')
        labels = class_text(card, 'mths3')
        if not source or not PROFILE_PATH.fullmatch(urlsplit(source).path) or not name or not place or not labels:
            continue
        row = public_person('www.teachtutti.co.uk', hashlib.sha256(source.encode()).hexdigest(),
                            name, place, [label.strip() for label in labels.split('·') if label.strip()], source)
        rows.append(dict(annotate_advert(row, description=class_text(card, 'i9')),
                         evidenceKind='directory_listing', listingUrl=url))
    return rows


def parse_profile(document, url):
    from directory_profiles import annotate_advert, public_person
    if not PROFILE_PATH.fullmatch(urlsplit(url).path):
        return None
    headers = [n for n in document.walk() if n.attrs.get('id') == 'npc1']
    if len(headers) != 1:
        return None
    names = [n for n in headers[0].walk() if n.tag == 'h1']
    places = [n for n in headers[0].walk() if n.has_class('ls3')]
    if len(names) != 1 or len(places) != 1 or not names[0].text() or not places[0].text():
        return None
    name, place = names[0].text(), places[0].text()
    def section(heading):
        matches = [n for n in document.walk() if n.has_class('pb3')
                   and n.first(lambda child: child.tag == 'h2' and child.text() == heading) is not None]
        return matches[0] if len(matches) == 1 else None

    subject_section = section('Subjects taught')
    tables = [n for n in subject_section.walk() if n.tag == 'table' and n.has_class('stt1')] if subject_section else []
    welcome = section('Welcome from ' + name)
    descriptions = [n for n in welcome.walk() if n.attrs.get('itemprop') == 'description'] if welcome else []
    if len(tables) != 1 or len(descriptions) != 1:
        return None
    subjects = []
    for row in tables[0].walk():
        # The CLI's DOM class belongs to __main__; imported parsing helpers must
        # use the node interface rather than require another module's class.
        cells = [n for n in row.children if getattr(n, 'tag', None) == 'td'] if row.tag == 'tr' else []
        if cells and cells[0].text():
            subjects.append(cells[0].text())
    if not subjects:
        return None
    description = descriptions[0].text()
    row = public_person('www.teachtutti.co.uk', hashlib.sha256(url.encode()).hexdigest(), name, place, subjects, url)
    # Only the named tutor's welcome text supplies these claims. A review,
    # neighbouring card or county heading cannot resolve or contradict locality.
    claims = list(dict.fromkeys(m.group(1) for m in re.finditer(
        r"\b(?:based|living|located|live)\s+in\s+([A-Za-z][A-Za-z '\-]{0,79}?)"
        r'(?=\s+(?:I|and|but|with|where|for|since|as|to|at)\b|[.,;!?]|$)', description, re.I)))
    row['additionalLocalityClaims'] = claims
    row['locationConflict'] = any(claim.casefold() != place.split(',', 1)[0].strip().casefold() for claim in claims)
    return annotate_advert(row, description=description)
