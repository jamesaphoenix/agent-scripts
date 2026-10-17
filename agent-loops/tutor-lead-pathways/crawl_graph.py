"""Export an evidence-backed domain graph from saved crawler artifacts, without fetching pages."""
import argparse
from collections import Counter
import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit, urlunsplit


def public_url(value):
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value.strip())
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return None
        if parts.hostname in ('localhost', '127.0.0.1', '::1'):
            return None
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or '/', parts.query, ''))
    except ValueError:
        return None


def domain(url):
    value = public_url(url)
    return urlsplit(value).hostname.removeprefix('www.').lower() if value else None


def timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return (parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)).astimezone(
            datetime.timezone.utc).isoformat()
    except (ValueError, TypeError):
        return None


class DomainGraph:
    def __init__(self):
        self.nodes = {}
        self.edges = {}
        self.observations = set()
        self.links = set()
        self.artifacts = {}
        self.errors = []
        self.event_counts = Counter()

    def node(self, url):
        host = domain(url)
        if not host:
            return None
        if host not in self.nodes:
            self.nodes[host] = {'id': host, 'urls': set(), 'fetched': set(), 'text': set(), 'held': set(),
                'titles': {}, 'runs': set(), 'sources': set(), 'errors': Counter(), 'events': [],
                'firstSeen': None, 'lastSeen': None, 'tutorSignal': False, 'profiles': {}, 'reviewed': set()}
        return self.nodes[host]

    def touch(self, node, date):
        if date:
            node['firstSeen'] = min(node['firstSeen'] or date, date)
            node['lastSeen'] = max(node['lastSeen'] or date, date)

    def link(self, source, target, kind, date, artifact, run, anchor=''):
        source, target = public_url(source), public_url(target)
        a, b = self.node(source), self.node(target)
        if not a or not b or a['id'] == b['id']:
            return
        identity = (source, target, kind, date)
        if identity in self.links:
            return
        self.links.add(identity)
        key = (a['id'], b['id'], kind)
        edge = self.edges.setdefault(key, {'source': a['id'], 'target': b['id'], 'kind': kind,
            'weight': 0, 'firstSeen': date, 'lastSeen': date, 'evidence': []})
        edge['weight'] += 1
        if date:
            edge['firstSeen'] = min(edge['firstSeen'] or date, date)
            edge['lastSeen'] = max(edge['lastSeen'] or date, date)
        if len(edge['evidence']) < 3:
            edge['evidence'].append({'sourceUrl': source, 'targetUrl': target, 'observedAt': date,
                                    'artifact': artifact, 'run': run, 'anchor': str(anchor)[:120]})
        for node in (a, b):
            node['runs'].add(run)
            self.touch(node, date)

    def observation(self, url, date, artifact, run, source, *, final_url=None, text=False,
                    title='', error=None, content_hash=None, links=(), tutor_signal=False, fetched=True):
        url = public_url(url)
        node = self.node(url)
        if node is None:
            return
        date = timestamp(date)
        identity = (url, date, content_hash or '', str(error or ''), bool(text), fetched)
        node['urls'].add(url)
        node['runs'].add(run)
        node['sources'].add(source)
        self.touch(node, date)
        if identity not in self.observations:
            self.observations.add(identity)
            self.event_counts[source] += 1
            if error:
                node['held'].add(url)
                node['errors'][str(error)[:250]] += 1
            elif fetched:
                node['fetched'].add(url)
            if text:
                node['text'].add(url)
            node['tutorSignal'] |= bool(tutor_signal)
            if title:
                node['titles'][url] = str(title)[:160]
            if len(node['events']) < 12:
                node['events'].append({'url': url, 'observedAt': date, 'source': source, 'run': run,
                    'outcome': 'held' if error else ('text' if text else ('fetched' if fetched else 'discovered')),
                    'error': str(error)[:250] if error else None, 'artifact': artifact})
        if final_url and domain(final_url) != node['id']:
            self.link(url, final_url, 'redirect', date, artifact, run)
        for link in links or []:
            target = link.get('url') if isinstance(link, dict) else link
            anchor = link.get('anchor', '') if isinstance(link, dict) else ''
            self.link(final_url or url, target, 'hyperlink', date, artifact, run, anchor)

    def checkpoint(self, data, artifact, run):
        for row in data.get('results') or []:
            node, evidence = row.get('node') or {}, row.get('evidence') or {}
            url = node.get('url') or evidence.get('url')
            date = row.get('fetchedAt') or data.get('updatedAt')
            self.observation(url, date, artifact, run, 'Go crawler', final_url=evidence.get('url'),
                text=bool(evidence.get('text')), title=evidence.get('title'), error=row.get('error'),
                content_hash=evidence.get('contentHash'), links=evidence.get('links'),
                tutor_signal=any(evidence.get(k) for k in ('candidateHint', 'elevenPlus', 'primary')))

    def attach_profiles(self, rows, reviewed=False):
        for row in rows:
            identity = row.get('canonicalId') or row.get('candidateId') or row.get('name')
            for url in [row.get('website'), *row.get('sourceUrls', [])]:
                host = domain(url)
                if host not in self.nodes:
                    continue
                node = self.nodes[host]
                if reviewed:
                    node['reviewed'].add(identity)
                else:
                    decision = row.get('decision') or {}
                    node['profiles'][identity] = {'name': row.get('name'), 'url': public_url(row.get('website')),
                        'subjects': row.get('matchedSubjects', row.get('services', [])),
                        'locality': row.get('localityClaim'), 'decisionKey': decision.get('decisionKey'),
                        'decision': decision.get('kind'), 'status': row.get('status'), 'outreachEligible': False}

    def result(self, metadata):
        incoming, outgoing = Counter(), Counter()
        for a, b, _ in self.edges:
            outgoing[a] += 1
            incoming[b] += 1
        nodes = []
        for host, n in sorted(self.nodes.items()):
            category = 'research' if n['profiles'] else ('crawled' if n['fetched'] else
                       ('held' if n['held'] else 'discovered'))
            urls = sorted(n['urls'])
            nodes.append({'id': host, 'category': category, 'urlCount': len(urls),
                'fetchedPages': len(n['fetched']), 'textPages': len(n['text']), 'heldPages': len(n['held']),
                'firstSeen': n['firstSeen'], 'lastSeen': n['lastSeen'], 'runs': sorted(n['runs']),
                'sources': sorted(n['sources']), 'tutorSignal': n['tutorSignal'],
                'inDegree': incoming[host], 'outDegree': outgoing[host],
                'urls': [{'url': u, 'title': n['titles'].get(u), 'fetched': u in n['fetched'],
                          'text': u in n['text'], 'held': u in n['held']} for u in urls[:10]],
                'errors': [{'reason': e, 'count': c} for e, c in n['errors'].most_common(5)],
                'events': n['events'], 'profiles': list(n['profiles'].values()),
                'reviewedProfileCount': len(n['reviewed'])})
        edges = [self.edges[k] for k in sorted(self.edges)]
        metadata.update({'nodeCount': len(nodes), 'edgeCount': len(edges),
            'domainsFetched': sum(n['fetchedPages'] > 0 for n in nodes),
            'domainsWithText': sum(n['textPages'] > 0 for n in nodes),
            'domainsHeldOnly': sum(n['heldPages'] > 0 and n['fetchedPages'] == 0 for n in nodes),
            'fetchedPages': sum(n['fetchedPages'] for n in nodes),
            'textPages': sum(n['textPages'] for n in nodes),
            'observationsBySource': dict(self.event_counts), 'artifactCount': len(self.artifacts),
            'readErrors': self.errors, 'artifactManifest': self.artifacts,
            'coverage': 'Saved Go checkpoints, referenced focused frontiers and public-profile fetch receipts. '
                'Edges are recorded hyperlinks, explicit frontier parent relationships, or cross-host redirects. '
                'Independent seeds are not connected artificially. Profile receipts contribute outcomes, not inferred links.',
            'privacy': 'Page body text, email and telephone contact fields, API credentials and paid request payloads are omitted.'})
        return {'schemaVersion': 1, 'meta': metadata, 'nodes': nodes, 'edges': edges}


def export(runtime, qualification, output, control_root=None):
    runtime, qualification = Path(runtime).resolve(), Path(qualification).resolve()
    job = json.loads((qualification / 'job.json').read_text())
    graph = DomainGraph()
    def relative(path):
        try:
            return str(path.relative_to(runtime))
        except ValueError:
            return path.name
    def read(path):
        raw = path.read_bytes()
        graph.artifacts[relative(path)] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)
    roots = list(dict.fromkeys([qualification, *map(Path, job.get('fetchRoots', [])),
                              *map(Path, job.get('crawlRoots', []))]))
    control = Path(control_root).resolve() if control_root else runtime / 'state/tutor-studio-20261017-free-12h'
    if control.exists():
        roots.append(control / 'review/autoresearch')
    checkpoint_paths = set()
    for root in roots:
        if root.exists():
            checkpoint_paths.update(root.rglob('crawl.json'))
    for path in sorted(checkpoint_paths):
        try:
            run = next((p.name for p in path.parents if p.parent == runtime / 'state'), path.parent.name)
            graph.checkpoint(read(path), relative(path), run)
        except (OSError, ValueError, TypeError, KeyError) as error:
            graph.errors.append({'artifact': relative(path), 'error': type(error).__name__})
    for raw in job.get('frontiers', []):
        path = Path(raw)
        if not path.exists():
            continue
        try:
            with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
                db.row_factory = sqlite3.Row
                # Frontier parent references are SHA-256 page keys, not necessarily URLs.
                query = ('SELECT p.url,COALESCE(parent.url,p.parent) AS parent_url,p.evidence,'
                         'p.state,p.reason,p.created_at FROM pages AS p '
                         'LEFT JOIN pages AS parent ON parent.key=p.parent')
                for row in db.execute(query):
                    evidence = json.loads(row['evidence']) if row['evidence'] else {}
                    if not isinstance(evidence, dict):
                        evidence = {}
                    date = row['created_at']
                    graph.observation(row['url'], date, relative(path), path.parent.name, 'Focused frontier',
                        text=bool(evidence.get('text')), title=evidence.get('title'),
                        # A held classification can still have a successful saved page fetch.
                        error=(row['reason'] or 'frontier ' + row['state'])
                            if not evidence.get('text') and row['state'] in ('error', 'failed', 'held', 'blocked') else None,
                        content_hash=evidence.get('contentHash'), links=evidence.get('links'),
                        tutor_signal=evidence.get('candidateHint', False), fetched=bool(evidence.get('text')))
                    if row['parent_url']:
                        graph.link(row['parent_url'], row['url'], 'crawl_path', timestamp(date), relative(path), path.parent.name)
            # SQLite is live: its transaction supplies a consistent read, rather than a misleading file hash.
            graph.artifacts[relative(path)] = 'read-only SQLite transaction'
        except (OSError, ValueError, sqlite3.Error) as error:
            graph.errors.append({'artifact': relative(path), 'error': type(error).__name__})
    for raw in job.get('profileRoots', []):
        root = Path(raw)
        for path in sorted((root / 'receipts').glob('*.json')):
            try:
                r = read(path)
                status = r.get('status')
                error = r.get('error') or (f'HTTP {status}' if status and not 200 <= int(status) < 300 else None)
                graph.observation(r.get('url'), r.get('observedAt'), relative(path), root.name,
                    'Profile crawler', error=error, content_hash=r.get('sha256'))
            except (OSError, ValueError, TypeError) as error:
                graph.errors.append({'artifact': relative(path), 'error': type(error).__name__})
    accepted_path = qualification / 'exports/automated-qualification/accepted.json'
    accepted = read(accepted_path) if accepted_path.exists() else []
    graph.attach_profiles(accepted)
    reviewed = []
    known = job.get('knownContacts')
    if known:
        reviewed.extend(read(Path(known['path'])))
    supplemental = qualification / 'exports/audited-subject-contacts.json'
    if supplemental.exists():
        reviewed.extend(read(supplemental))
    graph.attach_profiles(reviewed, reviewed=True)
    auth = control / 'authorization.json'
    current_start = json.loads(auth.read_text())['requestedAt'] if auth.exists() else job.get('startedAt')
    result = graph.result({'capturedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'qualificationRoot': relative(qualification), 'currentPassStartedAt': current_start,
        'modelQualifiedProfiles': len(accepted), 'reviewedContacts': len(reviewed),
        'profilesWithDecisions': sum(bool(r.get('decision', {}).get('decisionKey')) for r in accepted),
        'checkpointFiles': len(checkpoint_paths),
        'grouping': 'One node per observed hostname, merging only the www prefix. Subdomains remain distinct.'})
    output = Path(output)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')))
    output.chmod(0o600)
    return result['meta']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--qualification-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--control-root', type=Path)
    args = parser.parse_args()
    metadata = export(args.runtime_root, args.qualification_root, args.output, args.control_root)
    print(json.dumps({k: v for k, v in metadata.items() if k not in ('artifactManifest', 'readErrors')}, indent=2))
