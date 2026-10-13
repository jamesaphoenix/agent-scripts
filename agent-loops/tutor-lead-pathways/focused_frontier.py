#!/usr/bin/env python3
"""Yield-ranked, niche-gated crawl frontier. Model decisions never authorise outreach."""
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import urllib.parse

from keyword_expansion import canonical_url, search_navigation

TUTOR = re.compile(r'tutor|tuition|private teach|eleven[ -]*plus|11[ -]*(?:plus|\+)|maths? teacher|academic coach|dyslexia|phonics', re.I)
IDENTITY = re.compile(r'contact|about|profile|/t/|/tutor/|enquir|get.in.touch', re.I)
DIRECTORY = re.compile(r'directory|find.*tutor|tutor.*(?:list|search)|/results|/page/|[?&]page=', re.I)
DENIED = {'facebook.com', 'instagram.com', 'linkedin.com', 'twitter.com', 'x.com',
          'youtube.com', 'tiktok.com', 'pinterest.com', 'amazon.co.uk', 'amazon.com'}
BINARY = re.compile(r'\.(?:pdf|png|jpg|jpeg|webp|gif|svg|mp4|mp3|zip|woff2?|css|js)$', re.I)
DIRECTORY_HOSTS = {'findtutors.co.uk', 'tutorful.co.uk', 'superprof.co.uk', 'tutorhunt.com',
                   'firsttutors.com', 'uktutors.com', 'yell.com', 'bark.com', 'childcare.co.uk',
                   'tutorperch.com', 'tophelp.co.uk', 'cylex-uk.co.uk', 'hometutorsdirectory.co.uk',
                   'tutordex.net', 'tutorextra.co.uk'}
WEBSITE = re.compile(r'^(?:website|visit(?: my| our| tutor)? (?:website|site)|view website)$', re.I)
PROFILE_PATHS = {
    'tutorhunt.com': r'/users/[a-z0-9-]{1,64}/',
    'uktutors.com': r'/tutor/\d+/[a-z0-9-]+',
    'tutordex.net': r'/tutor/\d+/[a-z0-9-]+',
    'tutorextra.co.uk': r'/profile/[a-z0-9-]+/\d+-\d+',
    'childcare.co.uk': r'/profile/\d+',
    'tutorperch.com': r'/t/[a-z0-9]+(?:-[a-z0-9]+)+/?',
    'findtutors.co.uk': r'/(?:tutors-[^/]+|online-[^/]+)/[^/]+-\d+/?|/tutor/[a-z0-9-]+\.htm',
}


def profile_key(url):
    """A public profile URL key, not a reconciled person identity."""
    try:
        parsed = urllib.parse.urlsplit(url)
        if not parsed.hostname:
            return None
    except (ValueError, TypeError):
        return None
    site = site_identity(url)
    pattern = PROFILE_PATHS.get(site)
    return site + parsed.path.rstrip('/') if pattern and re.fullmatch(pattern, parsed.path) else None


def site_identity(url):
    host = urllib.parse.urlsplit(url).hostname.removeprefix('www.')
    labels = host.split('.')
    return '.'.join(labels[-3:]) if host.endswith(('.co.uk', '.org.uk', '.gov.uk', '.ac.uk', '.com.au')) else '.'.join(labels[-2:])


def admitted(url):
    url = canonical_url(url)
    if not url or search_navigation(url) or site_identity(url) in DENIED or BINARY.search(urllib.parse.urlsplit(url).path):
        return None
    # Drop session and calendar traps, retaining useful pagination and search filters.
    if len(url) > 1500 or re.search(r'[?&](?:sessionid|sid|replytocom)=', url, re.I):
        return None
    if site_identity(url) in DIRECTORY_HOSTS and re.fullmatch(
            r'/(?:login|signup|register|become-a-tutor|privacy|terms|tutor-bot)(?:\.asp|\.html)?/?',
            urllib.parse.urlsplit(url).path, re.I):
        return None
    return url


def link_signals(url, context, towns):
    text = url + ' ' + ' '.join(str(v) for v in context.values())
    cue = .95 if profile_key(url) else .9 if TUTOR.search(text) else .55 if IDENTITY.search(text) else .35 if DIRECTORY.search(text) else .1
    local = .8 if any(re.search(r'\b' + re.escape(t) + r'\b', text, re.I) for t in towns) else .2
    return cue, local


@dataclass(frozen=True)
class Policy:
    towns: list
    max_pages: int = 5000
    max_sites: int = 2000
    per_site: int = 8
    directory_per_site: int = 200
    max_depth: int = 2
    branch_sites: int = 15
    root_sites: int = 30
    max_hops: int = 6
    niche_threshold: float = .75
    kind_threshold: float = .65
    outside_threshold: float = .8
    exploration_every: int = 10

    def validate(self):
        if (not self.towns or not all(isinstance(t, str) and t for t in self.towns)
            or not 1 <= self.max_pages <= 10000 or not 1 <= self.max_sites <= 3000
            or not 1 <= self.per_site <= 8 or not 0 <= self.max_depth <= 2
            or not 1 <= self.directory_per_site <= 4000
            or not 0 <= self.branch_sites <= 15 or not 0 <= self.root_sites <= 50
            or not 1 <= self.max_hops <= 8 or not 1 <= self.exploration_every <= 100
            or not all(math.isfinite(v) and 0 <= v <= 1 for v in
                       (self.niche_threshold, self.kind_threshold, self.outside_threshold))):
            raise ValueError('Invalid bounded frontier policy')


class Frontier:
    def __init__(self, path, policy, known_profiles=()):
        policy.validate()
        self.path, self.policy = Path(path), policy
        self.known_profiles = frozenset(known_profiles)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS pages (
                key TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, site TEXT NOT NULL,
                root TEXT NOT NULL, parent TEXT, group_key TEXT NOT NULL, depth INTEGER NOT NULL,
                hops INTEGER NOT NULL, drift INTEGER NOT NULL, cue REAL NOT NULL, locality REAL NOT NULL,
                context TEXT NOT NULL, provenance TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, evidence TEXT, decision TEXT, reason TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS queue_state ON pages(state);
            CREATE INDEX IF NOT EXISTS queue_site ON pages(site);
            CREATE TABLE IF NOT EXISTS branch_sites (parent TEXT NOT NULL, site TEXT NOT NULL, PRIMARY KEY(parent,site));
            CREATE TABLE IF NOT EXISTS root_sites (root TEXT NOT NULL, site TEXT NOT NULL, PRIMARY KEY(root,site));
            CREATE TABLE IF NOT EXISTS source_yield (group_key TEXT NOT NULL, site TEXT NOT NULL,
                success INTEGER NOT NULL, PRIMARY KEY(group_key,site));
            CREATE TABLE IF NOT EXISTS branch_yield (root TEXT NOT NULL, site TEXT NOT NULL,
                success INTEGER NOT NULL, PRIMARY KEY(root,site));
        ''')
        raw = json.dumps(policy.__dict__, sort_keys=True)
        old = self.db.execute('SELECT value FROM policy WHERE id=1').fetchone()
        if old and old[0] != raw:
            self.db.close()
            raise ValueError('Frontier limits are immutable across restarts')
        with self.db: self.db.execute('INSERT OR IGNORE INTO policy VALUES(1,?)', (raw,))
        os.chmod(self.path, 0o600)

    def close(self):
        self.db.close()

    def add_many(self, rows):
        # A durable commit per batch avoids thousands of synchronous fsyncs
        # while preserving all-or-nothing admission and the same page limits.
        before = self.db.execute('SELECT count(*) FROM pages').fetchone()[0]
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            for row in rows:
                self.add(row['url'], row['context'], group=row['group'],
                         provenance=row['provenance'], evidence=row.get('evidence'), commit=False)
        return self.db.execute('SELECT count(*) FROM pages').fetchone()[0] - before

    def add(self, url, context, group='cached-search', parent=None, drift=0, provenance=None, evidence=None, commit=True):
        url = admitted(url)
        if not url:
            return None
        key, site = hashlib.sha256(url.encode()).hexdigest(), site_identity(url)
        existing = self.db.execute('SELECT key,evidence IS NULL AS needs_evidence FROM pages WHERE key=?', (key,)).fetchone()
        if existing:
            # Preserve discovery lineage and counters when a descendant also
            # appears in an upstream search result. Never manufacture a new root.
            if evidence and existing['needs_evidence']:
                self.db.execute('UPDATE pages SET evidence=? WHERE key=?', (json.dumps(evidence), key))
                if commit:
                    self.db.commit()
            return key
        if profile_key(url) in self.known_profiles:
            return None
        if self.db.execute('SELECT count(*) FROM pages').fetchone()[0] >= self.policy.max_pages * 4:
            return None
        count = self.db.execute('SELECT count(*) FROM pages WHERE site=?', (site,)).fetchone()[0]
        cap = self.policy.directory_per_site if site in DIRECTORY_HOSTS else self.policy.per_site
        if count >= cap:
            return None
        root, depth, hops = key, 0, 0
        if parent:
            root, group = parent['root'], parent['group_key']
            external = site != parent['site']
            depth, hops = parent['depth'] + int(external), parent['hops'] + 1
            if depth > self.policy.max_depth or hops > self.policy.max_hops:
                return None
            for table, column, value, cap in [('branch_sites', 'parent', parent['key'], self.policy.branch_sites),
                                             ('root_sites', 'root', root, self.policy.root_sites)]:
                if external and not self.db.execute(f'SELECT 1 FROM {table} WHERE {column}=? AND site=?', (value, site)).fetchone():
                    count = self.db.execute(f'SELECT count(*) FROM {table} WHERE {column}=?', (value,)).fetchone()[0]
                    if count >= cap:
                        return None
            if external:
                self.db.execute('INSERT OR IGNORE INTO branch_sites VALUES(?,?)', (parent['key'], site))
                self.db.execute('INSERT OR IGNORE INTO root_sites VALUES(?,?)', (root, site))
        cue, locality = link_signals(url, context, self.policy.towns)
        self.db.execute('''INSERT INTO pages(key,url,site,root,parent,group_key,depth,hops,drift,cue,locality,context,provenance,evidence)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (key, url, site, root, parent['key'] if parent else None, group,
            depth, hops, drift, cue, locality, json.dumps(context), json.dumps(provenance or {}),
            json.dumps(evidence) if evidence else None))
        if commit:
            self.db.commit()
        return key

    def recover(self):
        # Fetching uses a stable Go checkpoint per URL. Reclaiming does not
        # increase attempts or re-run a paid decision; the paid cache fences it.
        with self.db: self.db.execute("UPDATE pages SET state='pending' WHERE state='fetching'")

    def priority(self, row):
        counts = self.db.execute('SELECT count(*),coalesce(sum(success),0) FROM source_yield WHERE group_key=?',
                                 (row['group_key'],)).fetchone()
        posterior = (counts[1] + 1) / (counts[0] + 2)  # Beta(1,1), unique domains only.
        seen = self.db.execute("SELECT 1 FROM pages WHERE site=? AND state IN ('classified','held','error') LIMIT 1", (row['site'],)).fetchone()
        return .4 * row['cue'] + .15 * row['locality'] + .25 * posterior + .1 * (not seen) + .1 - .08 * row['depth'] - .04 * row['drift']

    def claim(self, exclude_sites=()):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            total = self.db.execute('SELECT coalesce(sum(attempts),0) FROM pages').fetchone()[0]
            sites = {r[0] for r in self.db.execute('SELECT DISTINCT site FROM pages WHERE attempts>0')}
            seen = {r[0] for r in self.db.execute("SELECT DISTINCT site FROM pages WHERE state IN ('classified','held','error')")}
            yields = {r[0]: (r[1], r[2]) for r in self.db.execute(
                'SELECT group_key,count(*),sum(success) FROM source_yield GROUP BY group_key')}
            branches = {r[0]: (r[1], r[2]) for r in self.db.execute('''
                SELECT b.root,sum(b.success=0),sum(b.success) FROM branch_yield b
                JOIN pages p ON p.key=b.root WHERE b.site<>p.site GROUP BY b.root''')}
            blocked = {r[0] for r in self.db.execute('''SELECT site FROM pages WHERE state='error'
                AND (reason LIKE 'Go fetch:%status 403%' OR reason LIKE 'Go fetch:%status 429%')
                GROUP BY site HAVING count(*)>=3''')}
            # Read queue metadata once. The former per-row queries and full text
            # reads made every claim slower as the frontier grew.
            rows = self.db.execute('''SELECT key,url,site,root,parent,group_key,depth,drift,cue,locality,
                attempts,evidence IS NOT NULL AS has_evidence FROM pages WHERE state='pending' ''').fetchall()
            eligible = []
            for row in rows:
                if profile_key(row['url']) in self.known_profiles:
                    self.db.execute("UPDATE pages SET state='known',reason='profile URL already in audited baseline' WHERE key=?", (row['key'],))
                    continue
                # Root result does not count as an explored child domain.
                failures, successes = branches.get(row['root'], (0, 0))
                if row['parent'] and failures >= 6 and successes == 0:
                    self.db.execute("UPDATE pages SET state='pruned',reason='six distinct child domains yielded no tutor' WHERE key=?", (row['key'],))
                    continue
                if row['site'] in blocked and not row['has_evidence']:
                    self.db.execute("UPDATE pages SET state='blocked',reason='host held after three access-denied responses' WHERE key=?", (row['key'],))
                    continue
                if row['site'] in exclude_sites:
                    continue
                if (row['attempts'] == 0 and total >= self.policy.max_pages) or (row['site'] not in sites and len(sites) >= self.policy.max_sites):
                    continue
                eligible.append(row)
            if not eligible:
                self.db.commit()
                return None
            # Ten percent exploration remains inside the admitted niche frontier.
            # It helps an untested directory compete with an established source.
            if total and total % self.policy.exploration_every == 0:
                row = min(eligible, key=lambda r: (yields.get(r['group_key'], (0, 0))[0], -r['cue'], r['key']))
            else:
                def rank(r):
                    count, success = yields.get(r['group_key'], (0, 0))
                    return (.4*r['cue'] + .15*r['locality'] + .25*(success+1)/(count+2)
                            + .1*(r['site'] not in seen) + .1 - .08*r['depth'] - .04*r['drift']
                            + .22*bool(profile_key(r['url'])) + .18*bool(r['parent'] and r['depth']))
                row = max(eligible, key=lambda r: (rank(r), -r['depth'], r['key']))
            self.db.execute("UPDATE pages SET state='fetching',attempts=max(attempts,1) WHERE key=?", (row['key'],))
            self.db.commit()
            return dict(self.db.execute('SELECT * FROM pages WHERE key=?', (row['key'],)).fetchone())
        except Exception:
            self.db.rollback()
            raise

    def hold(self, key, reason, state='held', evidence=None):
        if state not in ('held', 'error'):
            raise ValueError('Invalid terminal page state')
        with self.db:
            self.db.execute('UPDATE pages SET state=?,reason=?,evidence=coalesce(?,evidence) WHERE key=?',
                            (state, reason, json.dumps(evidence) if evidence else None, key))
            if state == 'error':
                row = self.db.execute('SELECT group_key,site FROM pages WHERE key=?', (key,)).fetchone()
                self.db.execute('INSERT OR IGNORE INTO source_yield VALUES(?,?,0)', (row['group_key'], row['site']))

    def complete(self, key, evidence, result):
        row = dict(self.db.execute('SELECT * FROM pages WHERE key=?', (key,)).fetchone())
        local_text = any(re.search(r'\b' + re.escape(t) + r'\b', evidence.get('text', ''), re.I)
                         for t in [*self.policy.towns, 'Buckinghamshire', 'Bucks', 'Milton Keynes'])
        strong = result['niche'] >= self.policy.niche_threshold and result['kindProbability'] >= self.policy.kind_threshold
        local = strong and result['local'] >= .7 and local_text and result['outside'] < self.policy.outside_threshold
        profile = bool(profile_key(row['url'])) and result['kind'] in ('independent_tutor', 'directory')
        success = int(local and (result['kind'] == 'independent_tutor' or profile))
        with self.db:
            self.db.execute("UPDATE pages SET state='classified',evidence=?,decision=?,reason=? WHERE key=?",
                            (json.dumps(evidence), json.dumps(dict(result, outreachEligible=False)),
                             ('local profile research candidate' if profile else 'local tutor research candidate')
                             if success else 'classified, not accepted as a local tutor', key))
            for table, column, value in [('source_yield', 'group_key', row['group_key']), ('branch_yield', 'root', row['root'])]:
                self.db.execute(f'''INSERT INTO {table} VALUES(?,?,?) ON CONFLICT({column},site)
                    DO UPDATE SET success=max(success,excluded.success)''', (value, row['site'], success))
        if result['outside'] >= self.policy.outside_threshold or result['kind'] in ('irrelevant', 'institution'):
            return 0
        positive = strong and result['kind'] in ('independent_tutor', 'directory')
        bridge = not positive and result['niche'] >= .45 and row['drift'] == 0
        if not positive and not bridge:
            return 0
        local_branch = result['local'] >= .5 or (result['kind'] == 'directory' and row['locality'] >= .5)
        links = evidence.get('links', [])[:2000]
        before = self.db.execute('SELECT count(*) FROM pages').fetchone()[0]
        identities = 0
        for link in sorted(links, key=lambda l: -link_signals(l.get('url', ''), {'anchor': l.get('anchor', '')}, self.policy.towns)[0]):
            url = admitted(link.get('url'))
            if not url:
                continue
            external = site_identity(url) != row['site']
            hint = url + ' ' + link.get('anchor', '')
            if external:
                # Directory profile "Website" links often point to a tutor's
                # personal domain without the word tutor in its name. Inspect
                # that destination once; it must pass its own page decision.
                tutor_website = row['site'] in DIRECTORY_HOSTS and WEBSITE.fullmatch(link.get('anchor', '').strip())
                if not positive or not local_branch or not (TUTOR.search(hint) or tutor_website):
                    continue
            elif not profile_key(url) and not TUTOR.search(hint) and not IDENTITY.search(hint) and not DIRECTORY.search(hint):
                continue
            if bridge or not local_branch:
                if external or not IDENTITY.search(hint) or identities >= 2:
                    continue
                identities += 1
            self.add(url, {'anchor': link.get('anchor', '')}, parent=row, drift=0 if positive else 1,
                     provenance={'parentDecisionKey': result['decisionKey'], 'parentUrl': row['url']})
        return self.db.execute('SELECT count(*) FROM pages').fetchone()[0] - before

    def summary(self):
        candidates = self.db.execute("SELECT url,site FROM pages WHERE reason IN ('local tutor research candidate','local profile research candidate')").fetchall()
        profiles = {profile_key(r['url']) for r in candidates if profile_key(r['url'])}
        return {'pages': self.db.execute('SELECT count(*) FROM pages').fetchone()[0],
                'attempts': self.db.execute('SELECT coalesce(sum(attempts),0) FROM pages').fetchone()[0],
                'states': dict(self.db.execute('SELECT state,count(*) FROM pages GROUP BY state')),
                'localTutorDomains': len({r['site'] for r in candidates if r['site'] not in DIRECTORY_HOSTS}),
                'localProfileCandidates': len(profiles),
                'profileCandidatesNotInBaseline': len(profiles - self.known_profiles),
                'outreachEligible': 0}
