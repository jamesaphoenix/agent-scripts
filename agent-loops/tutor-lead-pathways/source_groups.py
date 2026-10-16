"""Use the same tactic identity for observed yield and query selection."""
import re
from urllib.parse import unquote, urlsplit


def positive_sites(keyword):
    sites = set()
    for value in re.findall(r'(?<![-\w])site:([^\s()"]+)', keyword, re.I):
        try:
            host = urlsplit('https://' + value).hostname
            if host:
                sites.add(host.lower().removeprefix('www.'))
        except ValueError:
            continue
    return sites


def tactic_group(item):
    tactic = item.get('tactic', item.get('lane', 'unknown'))
    if tactic == 'recursive_keywords':
        keyword, town = item.get('payload', {}).get('keyword'), item.get('town')
        if isinstance(keyword, str) and isinstance(town, str) and town.strip():
            keyword, town = ' '.join(unquote(keyword).casefold().split()), ' '.join(town.casefold().split())
            suffix = ' ' + town
            phrase = keyword[:-len(suffix)] if keyword.endswith(suffix) else ''
            if re.fullmatch(r"[\w +&'()/-]{3,100}", phrase):
                # Explore each service phrase across the approved geography.
                # A few empty searches for one phrase cannot test every other.
                return tactic + ':' + phrase
    if tactic == 'public_profiles':
        sites = positive_sites(item.get('payload', {}).get('keyword', ''))
        if len(sites) == 1:
            return next(iter(sites))
    return tactic
