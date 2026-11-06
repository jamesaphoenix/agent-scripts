#!/usr/bin/env python3
"""Image-only Hetzner retention: all containers, native pins and two rollback bundles.

Requires an audited per-environment ledger. Never deletes release metadata,
containers, networks, volumes, dangling images or build caches. Defaults to plan.
"""
import argparse
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess

PROJECTS = ('octospark-staging', 'octospark-prod', 'trace-learn-staging', 'trace-learn-prod')
PREFIX = {'octospark': 'europe-west3-docker.pkg.dev/octospark-prod/tx-agent-kit/',
          'trace-learn': 'europe-west3-docker.pkg.dev/tracelearn-prod/trace-learn/'}
KEYS = {'API_IMAGE':'api', 'WORKER_IMAGE':'worker', 'ML_IMAGE':'ml',
        'RENDERER_IMAGE_AMD64':'renderer', 'MEDIA_RUNTIME_IMAGE_AMD64':'media-runtime'}
REPOS = {PREFIX['octospark'] + name for name in KEYS.values()} | {
    PREFIX['trace-learn'] + name for name in ('api','worker')}
NATIVE = Path('/var/lib/octospark-compute')
spec = importlib.util.spec_from_file_location('image_age', Path(__file__).parent/'lib/image-age.py')
age = importlib.util.module_from_spec(spec)
spec.loader.exec_module(age)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def product(project):
    return project.rsplit('-', 1)[0]


def repository(ref):
    return ref.split('@', 1)[0].split(':', 1)[0]


def docker(*args):
    result = subprocess.run(['docker', '--context', 'default', *args], capture_output=True,
                            text=True, timeout=60)
    if result.returncode:
        # Do not leak inspect output, environments, or credential-bearing errors.
        raise RuntimeError('Docker operation failed: ' + ' '.join(args[:2]))
    return result.stdout


def inspect_all(kind):
    args = ['ps', '-aq'] if kind == 'container' else ['image','ls','-aq','--no-trunc']
    ids = sorted(set(docker(*args).split()))
    records = []
    for start in range(0, len(ids), 40):
        batch = json.loads(docker(kind, 'inspect', *ids[start:start+40]))
        if len(batch) != len(ids[start:start+40]):
            raise ValueError('Incomplete Docker inventory')
        records.extend(batch)
    return records


def read_artifact(path, app):
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('Missing or symlinked artifact')
    data = path.read_bytes()
    fields = {}
    for line in data.decode().splitlines():
        if '=' not in line: continue
        key, value = line.split('=', 1)
        if key in fields: raise ValueError('Duplicate artifact field')
        fields[key] = value
    source = fields.get('SOURCE_SHA', '')
    if not re.fullmatch(r'[a-f0-9]{40}', source):
        raise ValueError('Missing artifact source SHA')
    required = ('API_IMAGE','WORKER_IMAGE') if app == 'trace-learn' else tuple(KEYS)
    images = {}
    for key in required:
        ref = fields.get(key, '')
        if not re.fullmatch(re.escape(PREFIX[app] + KEYS[key]) + r'@sha256:[a-f0-9]{64}', ref):
            raise ValueError('Missing or invalid immutable release image')
        images[key] = ref
    return {'artifact':str(path), 'artifactSha256':hashlib.sha256(data).hexdigest(),
            'sourceSha':source, 'images':images}


def validate_record(record, app):
    if not isinstance(record, dict) or not isinstance(record.get('artifact'), str):
        raise ValueError('Invalid retention receipt')
    actual = read_artifact(record['artifact'], app)
    if any(record.get(key) != value for key, value in actual.items()):
        raise ValueError('Retention receipt changed or incomplete')
    if not isinstance(record.get('evidence'), str) or not record['evidence']:
        raise ValueError('Missing receipt verification evidence')
    return record


def retain_history(history, current):
    retained = {}
    if set(history) != set(PROJECTS) or set(current) != set(PROJECTS):
        raise ValueError('Require every product and environment')
    for project in PROJECTS:
        chosen, seen = [], set()
        if not history[project] or history[project][-1]['images'] != current[project]['images']:
            raise ValueError('Current bundle is absent from audited success history: ' + project)
        # A daily observer can miss multiple deployments. Never promote current
        # implicitly: require an operator-verified complete success history.
        for record in reversed(history[project]):
            identity = tuple(sorted(record['images'].values()))
            if identity not in seen:
                chosen.append(record); seen.add(identity)
            if len(chosen) == 3: break
        if len(chosen) < 3:
            raise ValueError('Cannot establish two distinct rollback bundles for ' + project)
        retained[project] = list(reversed(chosen))
    return retained


def current_release(project, containers):
    app = product(project)
    services = {}
    for service in ('api','worker'):
        matches = [c for c in containers if c.get('Config',{}).get('Labels',{}).get('com.docker.compose.project') == project
                   and c.get('Config',{}).get('Labels',{}).get('com.docker.compose.service') == service]
        if len(matches) != 1 or matches[0]['State']['Status'] != 'running':
            raise ValueError('Missing or ambiguous current service for ' + project)
        if service == 'api' and matches[0]['State'].get('Health',{}).get('Status') != 'healthy':
            raise ValueError('Current API is not healthy: ' + project)
        if matches[0]['State'].get('Health',{}).get('Status','healthy') != 'healthy':
            raise ValueError('Current worker is not healthy: ' + project)
        services[service.upper() + '_IMAGE'] = matches[0]['Config']['Image']
    candidates = []
    for path in Path('/opt', app, 'deploy/artifacts').glob('images-*.env'):
        try: record = read_artifact(path, app)
        except (ValueError, OSError): continue  # legacy build artifacts cannot establish current
        if all(record['images'][key] == value for key, value in services.items()):
            candidates.append((path.stat().st_mtime_ns, record))
    if not candidates: raise ValueError('No immutable current artifact for ' + project)
    record = max(candidates, key=lambda item:item[0])[1]
    record['evidence'] = 'healthy-current-containers:' + project
    return record


def native_pins(current, retired=()):
    pins, proof_hashes = set(), {}
    for environment in ('staging','prod'):
        path = NATIVE / ('release-' + environment + '.json')
        if 'octospark-' + environment in retired and not path.exists():
            continue
        raw = path.read_bytes(); proof = json.loads(raw)
        if proof.get('environment') != environment or proof.get('canaryPassed') is not True:
            raise ValueError('Native proof is incomplete')
        if not re.fullmatch(r'[a-f0-9]{40}', proof.get('sourceSha','')):
            raise ValueError('Invalid native source SHA')
        for key, repo in [('rendererImage','renderer'),('mediaImage','media-runtime')]:
            ref = proof.get(key,'')
            if not re.fullmatch(re.escape(PREFIX['octospark']+repo) + r'@sha256:[a-f0-9]{64}', ref):
                raise ValueError('Invalid native image pin')
            artifact_key = 'RENDERER_IMAGE_AMD64' if key == 'rendererImage' else 'MEDIA_RUNTIME_IMAGE_AMD64'
            if current['octospark-' + environment]['images'][artifact_key] != ref:
                raise ValueError('Native bundle is absent from audited success history')
            pins.add(ref)
        proof_hashes[str(path)] = hashlib.sha256(raw).hexdigest()
    # A switch before proof publication must fail closed, including shared render.
    for name in ('current-media-staging','current-media-prod','current-render'):
        path = NATIVE / name
        optional = (name == 'current-media-staging' and 'octospark-staging' in retired
                    or name == 'current-media-prod' and 'octospark-prod' in retired
                    or name == 'current-render' and {'octospark-staging','octospark-prod'} <= set(retired))
        if optional and not path.exists(): continue
        if not path.is_symlink(): raise ValueError('Missing native current symlink')
        target = path.resolve(strict=True)
        record = read_artifact(target / 'images.env', 'octospark')
        pins.update(record['images'].values())
        proof_hashes[str(path)] = str(target) + ':' + record['artifactSha256']
    return pins, proof_hashes


def retired_projects(ledger):
    retired = ledger.get('retiredProjects', [])
    if (not isinstance(retired, list) or any(not isinstance(p, str) or p not in PROJECTS for p in retired)
            or len(retired) != len(set(retired))):
        raise ValueError('Invalid explicit retired-project state')
    return sorted(retired)


def current_states(history, containers, retired):
    return {project:history[project][-1] if project in retired else current_release(project, containers)
            for project in PROJECTS}


def snapshot(state_dir):
    ledger_path = state_dir / 'rollback-ledger.json'
    ledger = json.loads(ledger_path.read_text())
    if ledger.get('version') != 1 or set(ledger.get('projects',{})) != set(PROJECTS):
        raise ValueError('Require an audited ledger for all four environments')
    history = ledger['projects']
    for project, records in history.items():
        for record in records: validate_record(record, product(project))
    containers = inspect_all('container')
    retired = retired_projects(ledger)
    current = current_states(history, containers, retired)
    history = retain_history(history, current)
    refs, native = native_pins(current, retired)
    for records in history.values():
        for record in records: refs.update(record['images'].values())
    images = inspect_all('image')
    available_refs = {ref for item in images for ref in (item.get('RepoTags') or [])+(item.get('RepoDigests') or [])}
    missing = refs - available_refs
    if missing:
        # Retention cannot make an already-missing rollback safe. Operator must
        # rehydrate immutable digests before enabling deletion.
        raise ValueError('Protected image digests missing locally: ' + ','.join(sorted(missing)))
    identity = {'containers':sorted((c['Id'],c['Image'],c['Name']) for c in containers),
                'history':history, 'native':native, 'retiredProjects':retired}
    return {'fingerprint':digest(identity), 'history':history, 'protected':refs,
            'containers':containers, 'candidates':candidates(images, containers, refs)}


def candidates(images, containers, protected):
    container_ids = {c['Image'] for c in containers}
    result = []
    for item in images:
        refs = set((item.get('RepoTags') or []) + (item.get('RepoDigests') or []))
        if not refs or item['Id'] in container_ids or refs & protected:
            continue
        if not {repository(ref) for ref in refs} <= REPOS:
            continue
        try: old = age.old_enough(item, 48)
        except (ValueError, TypeError, AttributeError): old = False
        if old: result.append({key:item.get(key) for key in ('Id','RepoTags','RepoDigests','Created','Metadata')})
    return sorted(result, key=lambda item:(item['Created'],item['Id']))


def apply(plan, state_dir):
    for item in plan['candidates']:
        fresh = snapshot(state_dir)
        if fresh['fingerprint'] != plan['fingerprint']:
            raise ValueError('Protection metadata or container inventory changed; regenerate plan')
        inspected = json.loads(docker('image','inspect',item['Id']))
        if len(inspected) != 1 or candidates(inspected, fresh['containers'], fresh['protected']) != [item]:
            raise ValueError('Candidate changed; regenerate plan')
        # ID-based, non-forced deletion cannot remove an image a concurrent
        # container now uses. Multiple tags intentionally cause a safe refusal.
        docker('image','rm',item['Id'])
        print('Removed ' + item['Id'], flush=True)


def atomic_json(path, value):
    temp = path.with_suffix('.tmp')
    with temp.open('w') as stream:
        os.chmod(temp, 0o600); json.dump(value, stream, indent=2); stream.write('\n')
        stream.flush(); os.fsync(stream.fileno())
    temp.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, default=Path('/opt/host-docker-cleanup/state'))
    parser.add_argument('--plan-out', type=Path)
    parser.add_argument('--apply-plan', type=Path)
    parser.add_argument('--cron', action='store_true', help='Fresh plan then apply with all rechecks')
    args = parser.parse_args()
    if (args.state_dir/'disabled').exists(): print('Disabled; nothing changed.'); return
    with ExitStack() as stack:
        for path in [args.state_dir/'hetzner-images.lock', NATIVE/'deploy.lock']:
            handle = stack.enter_context(path.open('a'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        data = snapshot(args.state_dir)
        # Archive the verified ledger without changing its audited release order.
        history_path = args.state_dir/'receipt-archive'
        history_path.mkdir(exist_ok=True)
        archive = history_path/(digest(data['history'])+'.json')
        if not archive.exists(): atomic_json(archive, {'version':1,'projects':data['history']})
        plan = {key:data[key] for key in ('fingerprint','candidates','history')}
        plan['createdAt'] = datetime.now(timezone.utc).isoformat()
        if args.plan_out: atomic_json(args.plan_out, plan)
        print(json.dumps(plan, indent=2), flush=True)
        if args.apply_plan:
            approved = json.loads(args.apply_plan.read_text())
            if approved['fingerprint'] != data['fingerprint']:
                raise ValueError('Plan protection metadata changed')
            apply(approved, args.state_dir)
        elif args.cron:
            apply(plan, args.state_dir)


if __name__ == '__main__':
    try: main()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as error:
        raise SystemExit('Image cleanup refused: ' + str(error))
