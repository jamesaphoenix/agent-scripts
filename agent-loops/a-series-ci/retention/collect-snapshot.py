#!/usr/bin/env python3
"""Build a --from-snapshot bundle from a remote host using READ-ONLY commands over SSH.

Runs only `docker image ls`, `docker ps -a`, `docker inspect --format` (names and
image IDs, never the environment), `docker system df` and `cat`/`ls` of the project
metadata files. It never writes on the host, never uses sudo, and treats anything it
cannot read as 'unreadable' so the planner fails closed for that product.

    collect-snapshot.py user@host > snapshot.json
    jud-image-retention.py --from-snapshot snapshot.json
"""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

spec = importlib.util.spec_from_file_location('retention', Path(__file__).with_name('jud-image-retention.py'))
retention = importlib.util.module_from_spec(spec)
spec.loader.exec_module(retention)


def ssh(host, command):
    done = subprocess.run(['ssh', '-o', 'BatchMode=yes', host, command], capture_output=True, text=True, timeout=120)
    return done.returncode, done.stdout


def remote_project(host, root, project):
    base = '%s/%s' % (root, project)
    code, _ = ssh(host, 'test -e %s' % base)
    if code:
        return {'state': 'absent'}
    code, out = ssh(host, 'cat %s/current.json' % base)
    if code:
        return {'state': 'unreadable', 'error': 'current.json not readable by the SSH user'}
    try:
        raw = json.loads(out)
        current = {'release': raw['release'], 'refs': sorted(set(raw.get('images', {}).values())), 'sourceSha': raw.get('sourceSha')}
        code, out = ssh(host, 'cat %s/healthy-releases.json' % base)
        healthy = json.loads(out) if not code else None
        code, out = ssh(host, 'ls -1 %s/releases' % base)
        releases = []
        for name in out.split() if not code else []:
            path = '%s/releases/%s' % (base, name)
            refs = set()
            code, text = ssh(host, 'cat %s/release.json' % path)
            if not code:
                refs.update(json.loads(text).get('images', {}).values())
            code, text = ssh(host, 'cat %s/compose.json' % path)
            if not code:
                refs.update(retention.compose_refs(text))
            code, text = ssh(host, 'stat -c %%Y %s' % path)
            releases.append({'path': path, 'mtime': float(text) if not code else time.time(), 'refs': sorted(refs)})
        return {'state': 'ok', 'current': current, 'healthy': healthy, 'releases': releases}
    except (ValueError, KeyError) as exc:
        return {'state': 'unreadable', 'error': type(exc).__name__}


def main(host):
    config = retention.merge_config(None)
    jsonl = lambda cmd: [json.loads(l) for l in ssh(host, cmd)[1].splitlines() if l.strip()]
    snapshot = {
        'now': time.time(),
        'images': jsonl('docker image ls -a --digests --no-trunc --format json'),
        'containers': jsonl('docker ps -a --no-trunc --format json'),
        'container_images': [],
        'build_cache': next((r for r in jsonl('docker system df --format json') if r.get('Type') == 'Build Cache'), None),
        'projects': {p: remote_project(host, config['root'], p) for p in config['projects']},
    }
    template = "'{{.Name}} {{.Image}}'"
    for line in ssh(host, 'docker inspect --format %s $(docker ps -aq)' % template)[1].splitlines():
        if line.strip():
            name, image_id = line.split()
            snapshot['container_images'].append({'name': name, 'image_id': image_id})
    json.dump(snapshot, sys.stdout)


if __name__ == '__main__':
    main(sys.argv[1])
