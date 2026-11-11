#!/usr/bin/env python3
"""Release and image retention for the shared A Series (MinisForum) Docker daemon.

Ported from agent-loops/synology-retention/retention.py. Dry run is the default;
nothing is deleted without --apply. Rules:

* Per project under <root>/<project>/ keep the current release plus the two most
  recent healthy ones (healthy-releases.json), and every release younger than the
  grace period. Release directories beyond the kept set are pruned.
* Never remove an image referenced by ANY container (running or stopped) or by a
  kept release (release.json images or compose.json / compose yaml image lines).
* Only images whose every reference is in an explicit allow-list of registry
  repositories are candidates. Untagged, undigested leftovers become candidates
  after a longer grace period. Docker is never called with --force.
* Before deleting, take an exclusive flock on <root>/deploy.host.lock and on each
  project's deploy.lock. If any is busy the run is skipped.
* Fail closed: if a product has a project whose metadata is unreadable or that has
  no recorded healthy rollback yet, none of that product's repositories are touched.

Modes: default dry run, --apply, --report (largest unreferenced images and build
cache), --from-snapshot FILE (plan from a JSON bundle; touches neither Docker nor
the filesystem). Never prints container environment or any secret.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time

DEFAULT_CONFIG = {
    'root': '/srv/jud',
    'docker_host': 'unix:///var/run/docker.sock',
    'projects': ['octospark-staging', 'trace-learn-staging', 'octospark-prod', 'trace-learn-prod'],
    # product -> registry repositories whose images retention may remove.
    # Exactly the Synology list. The Octospark `renderer` and `media-runtime`
    # repositories also exist on this host but are pinned by the native compute
    # artifacts (current-render / media), which this tool does not read, so they are
    # opt-in through the config file only once that pin source is protected.
    'allow_repositories': {
        'octospark': ['europe-west3-docker.pkg.dev/octospark-prod/tx-agent-kit/' + r
                      for r in ('api', 'worker', 'ml')],
        'trace-learn': ['europe-west3-docker.pkg.dev/tracelearn-prod/trace-learn/' + r
                        for r in ('api', 'worker')],
    },
    'keep_healthy': 2,
    'grace_hours': 48,
    'dangling_days': 14,
    'max_release_dirs': 10,
    'max_images': 10,          # per run; 0 means unlimited (deliberate reclaim)
    'bootstrap_hold': True,    # hold a product's repos until a healthy rollback exists
}
HEX = re.compile(r'^[0-9a-f]{6,64}$')
UNITS = {'B': 1, 'KB': 1000, 'MB': 1000 ** 2, 'GB': 1000 ** 3, 'TB': 1000 ** 4,
         'KIB': 1024, 'MIB': 1024 ** 2, 'GIB': 1024 ** 3, 'TIB': 1024 ** 4}


def parse_size(text):
    """Docker's human sizes (2.11GB, 140.8GB, 0B) to bytes. Unknown becomes 0."""
    match = re.fullmatch(r'\s*(-?[0-9.]+(?:e[+-]?\d+)?)\s*([A-Za-z]*)\s*', str(text or ''))
    if not match:
        return 0
    return int(round(float(match.group(1)) * UNITS.get(match.group(2).upper() or 'B', 1)))


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if abs(n) < 1000 or unit == 'TB':
            return ('%d%s' % (n, unit)) if unit == 'B' else '%.1f%s' % (n, unit)
        n /= 1000


def parse_created(text):
    # "2026-11-11 14:16:27 +0000 GMT" and RFC 3339 are both accepted.
    text = str(text).strip()
    match = re.match(r'(\d{4}-\d\d-\d\d)[ T](\d\d:\d\d:\d\d)(?:\.\d+)?\s*(Z|[+-]\d\d:?\d\d)?', text)
    if not match:
        return 0.0
    zone = match.group(3) or '+0000'
    zone = '+0000' if zone == 'Z' else zone.replace(':', '')
    return datetime.strptime('%s %s %s' % (match.group(1), match.group(2), zone), '%Y-%m-%d %H:%M:%S %z').timestamp()


def repository(ref):
    name = ref.split('@', 1)[0]
    head, _, last = name.rpartition('/')
    return (head + '/' if head else '') + last.split(':', 1)[0]


def product(project):
    return project.rsplit('-', 1)[0]


def merge_config(override):
    config = json.loads(json.dumps(DEFAULT_CONFIG))
    config.update(override or {})
    return config


# ---------------------------------------------------------------- snapshot model

def group_images(rows):
    """Collapse `docker image ls -a --digests --format json` rows (one per tag or
    digest) into one record per image ID."""
    images = {}
    for row in rows:
        image_id = row['ID'] if row['ID'].startswith('sha256:') else 'sha256:' + row['ID']
        item = images.setdefault(image_id, {'id': image_id, 'refs': set(), 'size': 0, 'created': 0.0, 'untagged': False})
        repo, tag, digest = row.get('Repository', ''), row.get('Tag', ''), row.get('Digest', '')
        if repo and repo != '<none>':
            if tag and tag != '<none>':
                item['refs'].add(repo + ':' + tag)
            if digest and digest != '<none>':
                item['refs'].add(repo + '@' + digest)
            if (not tag or tag == '<none>') and (not digest or digest == '<none>'):
                item['refs'].add(repo)
        item['size'] = max(item['size'], parse_size(row.get('Size')))
        item['created'] = max(item['created'], parse_created(row.get('CreatedAt', '')))
    return list(images.values())


def name_variants(name):
    """Forms a container's Image string can take for the same reference."""
    out = {name}
    base, _, digest = name.partition('@')
    if digest:
        out.add(base)
        out.add(repository(base) + '@' + digest)
    if not digest and ':' not in base.rsplit('/', 1)[-1]:
        out.add(base + ':latest')
    return out


def container_refs(containers, images, resolved=None):
    """Return the set of image IDs referenced by any container, and warnings for
    container images that match nothing. `resolved` is optional output of
    `docker inspect --format '{{.Name}} {{.Image}}'` (exact image IDs)."""
    ids, warnings = set(), []
    for item in resolved or []:
        ids.add(item['image_id'] if item['image_id'].startswith('sha256:') else 'sha256:' + item['image_id'])
    for container in containers:
        name = container.get('Image', '')
        variants = name_variants(name)
        matched = False
        for image in images:
            bare = image['id'].split(':', 1)[1]
            refs = image['refs'] | {r + ':latest' for r in image['refs'] if ':' not in r.rsplit('/', 1)[-1] and '@' not in r}
            if variants & refs or image['id'] == name or (HEX.match(name) and bare.startswith(name)) \
                    or (name.startswith('sha256:') and bare.startswith(name.split(':', 1)[1])):
                ids.add(image['id'])
                matched = True
        if not matched:
            warnings.append('container %s image %s matches no listed image' % (container.get('Names', '?'), name))
    return ids, warnings


def bind_sources(containers):
    sources = set()
    for container in containers:
        for field in ('Mounts',):
            for part in str(container.get(field, '')).split(','):
                if part.startswith('/'):
                    sources.add(part)
        labels = dict(p.split('=', 1) for p in str(container.get('Labels', '')).split(',') if '=' in p)
        for key in ('com.docker.compose.project.config_files', 'com.docker.compose.project.working_dir'):
            for part in labels.get(key, '').split(','):
                if part.startswith('/'):
                    sources.add(part)
    return sources


def compose_refs(text):
    """Image references in a compose.json document or compose yaml text."""
    refs = set()
    try:
        document = json.loads(text)
        for service in (document.get('services') or {}).values():
            if isinstance(service, dict) and isinstance(service.get('image'), str):
                refs.add(service['image'])
        return refs
    except ValueError:
        pass
    for match in re.finditer(r'^\s*image:\s*["\']?([^\s"\'#]+)', text, re.M):
        refs.add(match.group(1))
    return refs


def history_entries(history, project):
    """healthy-releases.json may be a list, or a dict keyed by project (Synology)."""
    if isinstance(history, dict):
        history = history.get(project, history.get('releases', []))
    return [h for h in (history or []) if isinstance(h, dict) and (h.get('path') or h.get('release'))]


# -------------------------------------------------------------------- planning

def plan(snapshot, config, now):
    images = group_images(snapshot['images'])
    referenced_ids, warnings = container_refs(snapshot['containers'], images, snapshot.get('container_images'))
    mounted = bind_sources(snapshot['containers'])
    grace = config['grace_hours'] * 3600
    allowed_by_product = {p: set(r) for p, r in config['allow_repositories'].items()}
    held_products, kept_refs, release_removals = {}, set(), []
    summary = {}
    for project in config['projects']:
        data = snapshot['projects'].get(project, {'state': 'absent'})
        state = data.get('state', 'ok')
        info = {'state': state, 'kept': [], 'removed': [], 'held_reason': None}
        summary[project] = info
        if state == 'absent':
            continue
        if state != 'ok' or not data.get('current'):
            held_products.setdefault(product(project), 'metadata unreadable for ' + project)
            info['held_reason'] = data.get('error') or 'current.json unreadable'
            for release in data.get('releases', []):
                kept_refs.update(release.get('refs', []))
            continue
        current = data['current']
        releases = {r['path']: r for r in data.get('releases', [])}
        current_path = current.get('release')
        kept_paths = [current_path]
        healthy = []
        for entry in history_entries(data.get('healthy'), project):
            path = entry.get('path') or entry.get('release')
            if path != current_path and path in releases and path not in healthy:
                healthy.append(path)
        # Newest observation last in the file; take the most recent `keep_healthy`.
        kept_paths += healthy[-config['keep_healthy']:]
        if config['bootstrap_hold'] and not healthy:
            held_products.setdefault(product(project), 'no recorded healthy rollback for ' + project)
            info['held_reason'] = 'no recorded healthy rollback release'
        for ref in current.get('refs', []):
            kept_refs.add(ref)
        for path, release in releases.items():
            young = now - release['mtime'] < grace
            is_mounted = any(m == path or m.startswith(path.rstrip('/') + '/') for m in mounted)
            if path in kept_paths or young or is_mounted or info['held_reason']:
                kept_refs.update(release.get('refs', []))
                info['kept'].append(path)
            else:
                info['removed'].append(path)
                release_removals.append(path)
    releases_limited = sorted(release_removals)
    if config['max_release_dirs']:
        releases_limited = releases_limited[:config['max_release_dirs']]
    # Releases we will not remove this run stay protecting their images.
    for project, info in summary.items():
        by_path = {r['path']: r for r in snapshot['projects'].get(project, {}).get('releases', [])}
        for path in info['removed']:
            if path not in releases_limited:
                kept_refs.update(by_path[path].get('refs', []))
    candidates, protected = [], []
    for image in images:
        refs = image['refs']
        reason = None
        if image['id'] in referenced_ids:
            reason = 'referenced by a container'
        elif refs & kept_refs or image['id'] in kept_refs:
            reason = 'referenced by a kept release'
        elif now - image['created'] < grace:
            reason = 'inside grace period'
        if not refs:
            if reason is None and now - image['created'] < config['dangling_days'] * 86400:
                reason = 'dangling but inside grace period'
            if reason is None:
                candidates.append(dict(image, kind='dangling'))
            else:
                protected.append((image, reason))
            continue
        owner = {p for p, repos in allowed_by_product.items() if {repository(r) for r in refs} <= repos}
        if not owner:
            reason = reason or 'repository not in allow-list'
        elif owner & set(held_products):
            reason = reason or 'product held: ' + '; '.join(held_products[p] for p in owner & set(held_products))
        if reason:
            protected.append((image, reason))
        else:
            candidates.append(dict(image, kind='allow-listed'))
    candidates.sort(key=lambda i: i['created'])
    chosen = candidates[:config['max_images']] if config['max_images'] else candidates
    return {
        'projects': summary, 'held_products': held_products, 'warnings': warnings,
        'release_dirs': releases_limited, 'release_dirs_eligible': sorted(release_removals),
        'images': chosen, 'images_eligible': candidates,
        'protected_count': len(protected), 'image_count': len(images),
        'protected_reasons': _count(reason for _, reason in protected),
    }


def _count(items):
    out = {}
    for item in items:
        key = re.sub(r'(product held:).*', r'\1 hold', item)
        out[key] = out.get(key, 0) + 1
    return out


def report(snapshot, config, now, top=25):
    """Largest unreferenced images, per-repository totals and build cache."""
    images = group_images(snapshot['images'])
    referenced, _ = container_refs(snapshot['containers'], images, snapshot.get('container_images'))
    loose = sorted((i for i in images if i['id'] not in referenced), key=lambda i: -i['size'])
    by_repo = {}
    for image in loose:
        for repo in ({repository(r) for r in image['refs']} or {'<none>'}):
            row = by_repo.setdefault(repo, [0, 0])
            row[0] += 1
            row[1] += image['size']
    return {
        'images_total': len(images), 'referenced': len(referenced),
        'unreferenced': len(loose), 'unreferenced_bytes_naive': sum(i['size'] for i in loose),
        'largest': [{'id': i['id'][7:19], 'size': i['size'], 'age_days': round((now - i['created']) / 86400, 1),
                     'refs': sorted(i['refs'])[:2]} for i in loose[:top]],
        'by_repository': sorted(([r, c, s] for r, (c, s) in by_repo.items()), key=lambda r: -r[2]),
        'build_cache': snapshot.get('build_cache'),
        'note': 'Sizes are per-image and overlap through shared layers, so the real reclaim is smaller.',
    }


# ------------------------------------------------------------------ live access

class Docker:
    """Docker CLI bound to one explicit DOCKER_HOST. Never forces removal."""

    def __init__(self, host, binary='docker'):
        self.host, self.binary = host, binary

    def run(self, *args, timeout=120):
        env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME')}
        env['DOCKER_HOST'] = self.host
        done = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=timeout, env=env)
        if done.returncode:
            # Never echo stderr: it can carry environment values.
            raise RuntimeError('docker %s failed' % args[0])
        return done.stdout

    def jsonl(self, *args):
        return [json.loads(line) for line in self.run(*args).splitlines() if line.strip()]


def live_snapshot(config):
    docker = Docker(config['docker_host'])
    root = Path(config['root'])
    snap = {
        'images': docker.jsonl('image', 'ls', '-a', '--digests', '--no-trunc', '--format', 'json'),
        'containers': docker.jsonl('ps', '-a', '--no-trunc', '--format', 'json'),
        'projects': {},
    }
    ids = docker.run('ps', '-aq').split()
    snap['container_images'] = []
    for offset in range(0, len(ids), 50):
        # A fixed template: names and image IDs only, never the environment.
        for line in docker.run('inspect', '--format', '{{.Name}} {{.Image}}', *ids[offset:offset + 50]).splitlines():
            if line.strip():
                name, image_id = line.split()
                snap['container_images'].append({'name': name, 'image_id': image_id})
    snap['build_cache'] = next((r for r in docker.jsonl('system', 'df', '--format', 'json') if r.get('Type') == 'Build Cache'), None)
    for project in config['projects']:
        snap['projects'][project] = read_project(root / project)
    return snap


def read_project(directory):
    if not directory.exists():
        return {'state': 'absent'}
    try:
        current_raw = json.loads((directory / 'current.json').read_text())
        current = {'release': str(current_raw['release']), 'refs': sorted(set(current_raw.get('images', {}).values())),
                   'sourceSha': current_raw.get('sourceSha')}
        healthy = None
        if (directory / 'healthy-releases.json').exists():
            healthy = json.loads((directory / 'healthy-releases.json').read_text())
        releases = []
        releases_dir = directory / 'releases'
        for path in sorted(releases_dir.iterdir()) if releases_dir.is_dir() else []:
            if path.is_symlink() or not path.is_dir():
                continue
            refs, modified = set(), path.stat().st_mtime
            for child in path.iterdir():
                if child.is_symlink() or not child.is_file():
                    continue
                modified = max(modified, child.stat().st_mtime)
                if child.name == 'release.json':
                    refs.update(json.loads(child.read_text()).get('images', {}).values())
                elif child.name in ('compose.json', 'compose.yaml', 'compose.yml', 'docker-compose.yml'):
                    refs.update(compose_refs(child.read_text()))
            releases.append({'path': str(path), 'mtime': modified, 'refs': sorted(refs)})
        return {'state': 'ok', 'current': current, 'healthy': healthy, 'releases': releases}
    except (OSError, ValueError, KeyError) as exc:
        return {'state': 'unreadable', 'error': type(exc).__name__}


# ---------------------------------------------------------------------- output

def render(result, now):
    lines = []
    for project, info in result['projects'].items():
        lines.append('%s: state=%s kept=%d removable_releases=%d%s' % (
            project, info['state'], len(info['kept']), len(info['removed']),
            (' HELD (%s)' % info['held_reason']) if info['held_reason'] else ''))
    for p, why in result['held_products'].items():
        lines.append('product %s held: %s' % (p, why))
    for path in result['release_dirs']:
        lines.append('would remove release dir %s' % path)
    total = 0
    for image in result['images']:
        total += image['size']
        lines.append('would remove image %s %s %s (%s, %.1f days old)' % (
            image['id'][7:19], human(image['size']), ','.join(sorted(image['refs'])) or '<dangling>', image['kind'],
            (now - image['created']) / 86400))
    eligible = result['images_eligible']
    lines.append('SUMMARY images=%d/%d eligible, bytes=%d (%s) this run, eligible bytes=%d (%s), release dirs=%d, '
                 'images kept or protected=%d of %d' % (
                     len(result['images']), len(eligible), total, human(total),
                     sum(i['size'] for i in eligible), human(sum(i['size'] for i in eligible)),
                     len(result['release_dirs']), result['protected_count'], result['image_count']))
    lines.append('protected because: ' + json.dumps(result['protected_reasons'], sort_keys=True))
    lines.extend('warning: ' + w for w in result['warnings'])
    return '\n'.join(lines)


def render_report(data):
    lines = ['images=%d referenced=%d unreferenced=%d unreferenced size (naive sum)=%s' % (
        data['images_total'], data['referenced'], data['unreferenced'], human(data['unreferenced_bytes_naive']))]
    if data.get('build_cache'):
        lines.append('build cache: %s' % json.dumps(data['build_cache']))
    lines.append('by repository (unreferenced images, count, bytes):')
    lines.extend('  %-90s %5d %10s' % (r, c, human(s)) for r, c, s in data['by_repository'][:15])
    lines.append('largest unreferenced images:')
    lines.extend('  %s %9s %6.1fd %s' % (i['id'], human(i['size']), i['age_days'], ','.join(i['refs']) or '<dangling>')
                 for i in data['largest'])
    lines.append(data['note'])
    return '\n'.join(lines)


def apply(result, config, docker, state_dir=None):
    root = Path(config['root'])
    outcome = {'removed_releases': [], 'removed_images': [], 'errors': []}
    with ExitStack() as stack:
        paths = [root / 'deploy.host.lock'] + [root / p / 'deploy.lock' for p in config['projects']
                                                if (root / p).exists()]
        for path in paths:
            if path.is_symlink():
                raise ValueError('Unsafe lock path ' + str(path))
            handle = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                outcome['skipped'] = 'lock busy: ' + str(path)
                return outcome
        for path in result['release_dirs']:
            path = Path(path)
            if path.is_symlink() or path.resolve().parent.parent.parent != root.resolve():
                outcome['errors'].append('unsafe release path ' + str(path))
                continue
            shutil.rmtree(path)
            outcome['removed_releases'].append(str(path))
        started = time.monotonic()
        for image in result['images']:
            if time.monotonic() - started > 900:
                outcome['errors'].append('time budget exhausted')
                break
            targets = sorted(image['refs']) or [image['id']]
            try:
                for target in targets:
                    docker.run('image', 'rm', target)   # never --force
                outcome['removed_images'].append(image['id'])
            except (RuntimeError, subprocess.TimeoutExpired):
                outcome['errors'].append('image removal skipped: ' + image['id'][7:19])
    return outcome


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--dry-run', action='store_true', help='default: print what would be removed')
    mode.add_argument('--apply', action='store_true', help='delete eligible releases and images')
    parser.add_argument('--report', action='store_true', help='report largest unreferenced images and build cache')
    parser.add_argument('--config', help='JSON overrides for the built-in defaults')
    parser.add_argument('--from-snapshot', help='plan from a snapshot JSON file; no Docker or filesystem access')
    parser.add_argument('--max-images', type=int, help='cap images removed per run (0 = unlimited)')
    parser.add_argument('--now', type=float, help='override the clock (snapshot mode)')
    parser.add_argument('--top', type=int, default=25)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    config = merge_config(json.loads(Path(args.config).read_text()) if args.config else None)
    if args.max_images is not None:
        config['max_images'] = args.max_images
    if args.from_snapshot:
        if args.apply:
            parser.error('--apply cannot be combined with --from-snapshot')
        snapshot = json.loads(Path(args.from_snapshot).read_text())
        now = args.now or snapshot.get('now') or time.time()
    else:
        snapshot, now = live_snapshot(config), time.time()
    if args.report:
        data = report(snapshot, config, now, args.top)
        print(json.dumps(data, indent=2) if args.json else render_report(data))
        return 0
    result = plan(snapshot, config, now)
    if args.json:
        print(json.dumps(result, indent=2, default=sorted))
    else:
        print(render(result, now))
    if args.apply:
        outcome = apply(result, config, Docker(config['docker_host']))
        print(json.dumps(outcome))
        return 1 if outcome['errors'] else 0
    print('dry run: nothing removed (use --apply to delete)', file=sys.stderr if args.json else sys.stdout)
    return 0


if __name__ == '__main__':
    sys.exit(main())
