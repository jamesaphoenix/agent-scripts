"""Use the same public-source identity for observed yield and query selection."""
import re
from urllib.parse import urlsplit


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
    if tactic == 'public_profiles':
        sites = positive_sites(item.get('payload', {}).get('keyword', ''))
        if len(sites) == 1:
            return next(iter(sites))
    return tactic
