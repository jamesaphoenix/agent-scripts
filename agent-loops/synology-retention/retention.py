#!/usr/bin/env python3
"""Bounded NAS release retention. Executed locally by DSM Task Scheduler.

Only current.json receipts whose running services pass health checks enter the
rollback ledger. Until a project has a recorded older healthy release, nothing
from that project's repository is deleted. No global Docker prune is used.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

ROOT = Path('/volume1/docker/jud')
DOCKER = '/usr/local/bin/docker'
PROJECTS = ('octospark-staging', 'octospark-prod', 'trace-learn-staging', 'trace-learn-prod')
REPOS = {
    'octospark': {'europe-west3-docker.pkg.dev/octospark-prod/tx-agent-kit/' + r for r in ('api', 'worker', 'ml')},
    'trace-learn': {'europe-west3-docker.pkg.dev/tracelearn-prod/trace-learn/' + r for r in ('api', 'worker')},
}
AGE = 48 * 3600
KEEP = 3
LIMIT = 10
DANGLING_AGE = 14 * 24 * 3600


def app(project):
    return project.rsplit('-', 1)[0]


def repository(ref):
    return ref.split('@', 1)[0].split(':', 1)[0]


def created_at(value):
    # Synology's Python predates arbitrary ISO fractional-second support.
    return datetime.fromisoformat(re.sub(r'(\.\d{6})\d+', r'\1', value.replace('Z', '+00:00'))).timestamp()


def identity(release):
    return (release['sourceSha'], tuple(sorted(release['images'].values())))


def plan(snapshot, old_history, now):
    protected_refs = set()
    bootstrap_repos = set()
    history = {}
    mounted_paths = [source for c in snapshot['containers'] for source in c.get('mountSources', [])]
    result = {'releasePaths': [], 'imageIds': [], 'bootstrapProjects': []}
    for project, data in snapshot['projects'].items():
        current = dict(data['current'], healthyObservedAt=now)
        releases = {r['path']: r for r in data['releases']}
        # A receipt is useful only while its unchanged release directory exists.
        observed = [r for r in old_history.get(project, [])
                    if r['path'] in releases and identity(r) == identity(releases[r['path']])]
        observed = [r for r in observed if r['path'] != current['path']] + [current]
        chosen = []
        seen = set()
        for record in reversed(observed):
            key = identity(record)
            if key not in seen:
                chosen.append(record)
                seen.add(key)
            if len(chosen) == KEEP:
                break
        history[project] = list(reversed(chosen))
        protected_paths = {r['path'] for r in chosen}
        bootstrap = len(chosen) < 2
        if bootstrap:
            result['bootstrapProjects'].append(project)
            bootstrap_repos.update(REPOS[app(project)])
        for release in data['releases']:
            mounted = any(p == release['path'] or p.startswith(release['path'].rstrip('/') + '/') for p in mounted_paths)
            if bootstrap or mounted or release['path'] in protected_paths or now - release['modified'] < AGE:
                protected_refs.update(release['images'].values())
            else:
                result['releasePaths'].append(release['path'])
    container_images = {c['Image'] for c in snapshot['containers']}
    allowed = set().union(*REPOS.values()) - bootstrap_repos
    for image in snapshot['images']:
        refs = set(image['refs'])
        owned = (refs and {repository(r) for r in refs} <= allowed
                 and not refs.intersection(protected_refs)
                 and now - image['created'] >= AGE)
        # A replaced local tag (for example Solar ROI :latest) leaves an image
        # with no tag or digest. Keep two weeks of these for manual recovery;
        # Docker itself still refuses to remove an image used by a container.
        dangling = not refs and now - image['created'] >= DANGLING_AGE
        if (owned or dangling) and image['id'] not in container_images:
            result['imageIds'].append(image['id'])
    # Do not strand an unremoved release folder by removing its images first.
    result['releasePaths'] = sorted(result['releasePaths'])[:LIMIT]
    retained_after_limit = {ref for p in snapshot['projects'].values() for r in p['releases']
                            if r['path'] not in result['releasePaths'] for ref in r['images'].values()}
    result['imageIds'] = [i['id'] for i in sorted(snapshot['images'], key=lambda image: image['created'])
                          if i['id'] in result['imageIds'] and not set(i['refs']) & retained_after_limit][:LIMIT]
    return result, history


def checked_child(parent, path):
    parent, path = Path(parent), Path(path)
    if parent.resolve() != parent or parent.is_symlink() or path.is_symlink() or path.parent != parent or path.resolve().parent != parent:
        raise ValueError('Unsafe release path: ' + str(path))
    return path


def tree_modified(path):
    if path.is_symlink():
        raise ValueError('Symlink in release: ' + str(path))
    latest = path.stat().st_mtime
    if path.is_dir():
        for child in path.iterdir():
            latest = max(latest, tree_modified(child))
    elif not path.is_file():
        raise ValueError('Non-regular release entry: ' + str(path))
    return latest


def docker(*args):
    # Never print inspect output or command failures: container env can be secret.
    done = subprocess.run([DOCKER, *args], capture_output=True, text=True, timeout=45)
    if done.returncode:
        raise RuntimeError('Docker operation failed: ' + args[0])
    return done.stdout


def inspect_all(kind):
    ids = sorted(set(docker(*(['ps', '-aq'] if kind == 'container' else ['image', 'ls', '-aq', '--no-trunc'])).split()))
    result = []
    for offset in range(0, len(ids), 50):
        result.extend(json.loads(docker(kind, 'inspect', *ids[offset:offset + 50])))
    return result


def read_release(project, path, require_source=False):
    checked_child(ROOT / project / 'releases', path)
    modified = tree_modified(path)
    record = json.loads((path / 'release.json').read_text())
    source = record.get('sourceSha')
    if record['project'] != project or (source is not None and not re.fullmatch(r'[0-9a-f]{7,40}', source)) or (require_source and source is None):
        raise ValueError('Invalid release identity')
    if not record['images'] or any(not re.fullmatch(re.escape(repository(ref)) + r'@sha256:[0-9a-f]{64}', ref)
                                   or repository(ref) not in REPOS[app(project)] for ref in record['images'].values()):
        raise ValueError('Unexpected release image repository')
    # Early migration attempts predate sourceSha. They are never observed as
    # healthy rollback receipts, but their image references must be protected
    # during bootstrap and the age grace period.
    return {'path': str(path), 'sourceSha': source or ('unverified:' + path.name), 'images': record['images'], 'modified': modified}


def snapshot():
    containers = inspect_all('container')
    projects = {}
    for project in PROJECTS:
        receipt = json.loads((ROOT / project / 'current.json').read_text())
        current = read_release(project, Path(receipt['release']), require_source=True)
        if receipt['images'] != current['images'] or receipt['sourceSha'] != current['sourceSha']:
            raise ValueError('Current receipt does not match immutable release')
        required = ['api', 'worker', 'mcp', 'redis'] if app(project) == 'octospark' else ['api', 'worker', 'cloudsql-proxy']
        for service in required:
            matches = [c for c in containers if c['Config'].get('Labels', {}).get('com.docker.compose.project') == project
                       and c['Config']['Labels'].get('com.docker.compose.service') == service and c['State']['Running']]
            if len(matches) != 1 or matches[0]['State'].get('Health', {}).get('Status', 'running') not in ('healthy', 'running'):
                raise RuntimeError('Service not healthy: ' + project + '/' + service)
            key = 'WORKER_IMAGE' if service == 'worker' else 'API_IMAGE'
            if service in ('api', 'mcp', 'worker') and matches[0]['Config']['Image'] != current['images'][key]:
                raise RuntimeError('Service image differs from successful receipt')
            if service in ('api', 'mcp', 'worker'):
                environment = dict(value.split('=', 1) for value in matches[0]['Config']['Env'])
                if environment.get('BUILD_COMMIT_SHA') != current['sourceSha']:
                    raise RuntimeError('Service source differs from successful receipt')
        with urllib.request.urlopen('http://127.0.0.1:' + str(int(receipt['apiPort'])) + '/health', timeout=8) as response:
            health = json.load(response)
        # Octospark health has no commit field; its container source and digest
        # were checked above. Trace Learn also exposes the source publicly.
        if health.get('status') not in ('ok', 'healthy') or health.get('commit', current['sourceSha']) != current['sourceSha']:
            raise RuntimeError('API health or source identity mismatch: ' + project)
        releases = [read_release(project, p) for p in (ROOT / project / 'releases').iterdir()]
        projects[project] = {'current': current, 'releases': releases}
    images = [{'id': i['Id'], 'refs': (i.get('RepoTags') or []) + (i.get('RepoDigests') or []),
               'created': created_at(i['Created']), 'size': i['Size']}
              for i in inspect_all('image')]
    return {'projects': projects, 'containers': [{'Image': c['Image'], 'mountSources': [m['Source'] for m in c.get('Mounts', []) if m.get('Source')]} for c in containers], 'images': images}


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    if path.is_symlink() or temp.is_symlink():
        raise ValueError('Unsafe state path')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def run(apply=False):
    os.umask(0o077)
    state = ROOT / 'maintenance'
    if ROOT.is_symlink() or ROOT.resolve() != ROOT or state.is_symlink():
        raise ValueError('Unsafe maintenance root')
    state.mkdir(mode=0o700, exist_ok=True)
    result = {'checkedAt': time.time(), 'mode': 'apply' if apply and (state / 'cleanup-enabled').is_file() else 'observe',
              'removedReleasePaths': [], 'removedImageIds': [], 'errors': []}
    started = time.monotonic()
    try:
        with ExitStack() as stack:
            # Same locks as both products' receiver. Never race deployment or another janitor.
            for path in [state / 'cleanup.lock'] + [ROOT / p / 'deploy.lock' for p in PROJECTS]:
                if path.is_symlink() or path.parent.resolve() != path.parent:
                    raise ValueError('Unsafe lock path')
                handle = stack.enter_context(path.open('a'))
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            data = snapshot()
            history_path = state / 'healthy-releases.json'
            if history_path.is_symlink():
                raise ValueError('Unsafe history path')
            old_history = json.loads(history_path.read_text()) if history_path.exists() else {}
            pending, history = plan(data, old_history, time.time())
            atomic_json(history_path, history)
            result.update(pending)
            result['freeBytesBefore'] = shutil.disk_usage(ROOT).free
            if result['mode'] == 'apply':
                if not shutil.rmtree.avoids_symlink_attacks:
                    raise RuntimeError('Host lacks safe directory removal support')
                for path in pending['releasePaths']:
                    if time.monotonic() - started > 300:
                        raise RuntimeError('Cleanup time budget exhausted')
                    path = Path(path)
                    checked_child(path.parent, path)
                    tree_modified(path)
                    shutil.rmtree(path)
                    result['removedReleasePaths'].append(str(path))
                for image_id in pending['imageIds']:
                    if time.monotonic() - started > 300:
                        raise RuntimeError('Cleanup time budget exhausted')
                    try:
                        # Docker refuses images referenced by any container. Never force deletion.
                        docker('image', 'rm', image_id)
                        result['removedImageIds'].append(image_id)
                    except (RuntimeError, subprocess.TimeoutExpired):
                        result['errors'].append('Image removal skipped: ' + image_id)
            result['freeBytesAfter'] = shutil.disk_usage(ROOT).free
    except BlockingIOError:
        result['skipped'] = 'deployment or cleanup lock busy'
    except Exception as exc:
        result['errors'].append(type(exc).__name__ + ': ' + str(exc)[:240])
    atomic_json(state / 'retention-last.json', result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true', help='Delete eligible items only when cleanup-enabled exists')
    output = run(parser.parse_args().apply)
    print(json.dumps(output))
    raise SystemExit(1 if output['errors'] else 0)
