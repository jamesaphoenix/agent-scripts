#!/usr/bin/env python3
"""Keep current and two observed healthy NAS image versions in Artifact Registry."""
import argparse
import json
from pathlib import Path
import re
import shlex
import subprocess

ROOT = 'europe-west3-docker.pkg.dev'
REPOS = {
    'octospark': ('octospark-prod/tx-agent-kit', ('api', 'worker', 'ml')),
    'trace-learn': ('tracelearn-prod/trace-learn', ('api', 'worker')),
}
IMAGE = re.compile(r'^(?P<name>.+)@sha256:[0-9a-f]{64}$')


def ssh_json(host, path):
    result = subprocess.run(
        ['ssh', '-o', 'BatchMode=yes', host, 'cat ' + shlex.quote(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


def pins(app, environment, current, history):
    repo, packages = REPOS[app]
    project = app + '-' + environment
    if current.get('project') != project or not isinstance(history, list):
        raise ValueError('NAS release receipt mismatch')
    releases = [current] + sorted(history, key=lambda row: row.get('healthyObservedAt', 0), reverse=True)
    result = {}
    for package in packages:
        refs = []
        for release in releases:
            if release.get('project', project) != project:
                raise ValueError('NAS release history mismatch')
            ref = release.get('images', {}).get(package.upper().replace('-', '_') + '_IMAGE')
            if ref is None:
                continue
            match = IMAGE.fullmatch(ref)
            if not match or match.group('name') != f'{ROOT}/{repo}/{package}':
                raise ValueError('Unexpected NAS image reference: ' + str(ref))
            if ref not in refs:
                refs.append(ref)
            if len(refs) == 3:
                break
        if not refs:
            raise ValueError('No NAS image reference for ' + package)
        for slot, ref in enumerate(refs):
            result[f'{package}:nas-retain-{environment}-{slot}'] = (ref, f'{ROOT}/{repo}/{package}:nas-retain-{environment}-{slot}')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', choices=REPOS)
    parser.add_argument('environment', choices=('staging', 'prod'))
    parser.add_argument('--host', default='mydocker@100.123.72.113')
    parser.add_argument('--snapshot', type=Path, help='Offline JSON containing current and history')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    project = args.app + '-' + args.environment
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text())
        current, history = snapshot['current'], snapshot['history']
    else:
        current = ssh_json(args.host, f'/volume1/docker/jud/{project}/current.json')
        history = ssh_json(args.host, '/volume1/docker/jud/maintenance/healthy-releases.json').get(project, [])
    selected = pins(args.app, args.environment, current, history)
    for name, (ref, tag) in selected.items():
        if args.apply:
            subprocess.run(['gcloud', 'artifacts', 'docker', 'tags', 'add', ref, tag, '--quiet'], check=True, timeout=120)
        print(name + ' ' + ref.rsplit(':', 1)[-1][:12] + (' pinned' if args.apply else ' planned'))


if __name__ == '__main__':
    main()
