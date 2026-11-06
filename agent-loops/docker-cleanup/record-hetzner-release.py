#!/usr/bin/env python3
"""Called by the final successful release gate, never by an intermediate build."""
import argparse
from contextlib import ExitStack
import fcntl
import importlib.util
import json
from pathlib import Path
import re

spec=importlib.util.spec_from_file_location('hetzner_cleanup',Path(__file__).with_name('cleanup-hetzner-images.py'))
cleanup=importlib.util.module_from_spec(spec);spec.loader.exec_module(cleanup)


def validate_inputs(project, artifact, evidence, native_only=False):
    app=cleanup.product(project)
    if native_only:
        if project not in ('octospark-staging','octospark-prod') or artifact is not None:
            raise ValueError('Native-only recording requires an Octospark environment and no artifact override')
        if not re.fullmatch(r'https://github\.com/Just-Understanding-Data-Ltd/octospark/actions/runs/[1-9][0-9]*',evidence):
            raise ValueError('Require the owning product release run URL')
        return
    expected=Path('/opt')/app/'deploy/artifacts'
    path=Path(artifact)
    repo='smart-cub-learning' if app=='trace-learn' else app
    if project not in cleanup.PROJECTS or path.parent != expected or not re.fullmatch(r'images-[a-f0-9]+(?:-amd64|-standby)?\.env',path.name):
        raise ValueError('Invalid release artifact path')
    if not re.fullmatch('https://github.com/Just-Understanding-Data-Ltd/'+repo+r'/actions/runs/[1-9][0-9]*',evidence):
        raise ValueError('Require the owning product release run URL')


def save_ledger(state_dir, ledger):
    path=state_dir/'rollback-ledger.json'
    old=json.loads(path.read_text())
    if old == ledger: return
    archive=state_dir/'receipt-archive';archive.mkdir(exist_ok=True)
    backup=archive/(cleanup.digest(old)+'.json')
    if not backup.exists(): cleanup.atomic_json(backup,old)
    cleanup.atomic_json(path,ledger)


def append_receipt(state_dir, project, record, native_record=None):
    path=state_dir/'rollback-ledger.json'
    ledger=json.loads(path.read_text())
    if ledger.get('version') != 1 or set(ledger.get('projects',{})) != set(cleanup.PROJECTS):
        raise ValueError('Require existing audited bootstrap ledger')
    if record is not None and project in cleanup.retired_projects(ledger):
        raise ValueError('Project is explicitly retired; operator must reactivate it first')
    for name, records in ledger['projects'].items():
        for old in records: cleanup.validate_record(old,cleanup.product(name))
    if native_record is not None:
        if project not in ('octospark-staging','octospark-prod'):
            raise ValueError('Native receipt requires Octospark environment')
        history=ledger.get('nativeProjects',{})
        if set(history) != set(cleanup.NATIVE_ENVIRONMENTS) or any(not history[e] for e in cleanup.NATIVE_ENVIRONMENTS):
            raise ValueError('Require separately audited native bootstrap history')
        for environment, records in history.items():
            for old in records: cleanup.validate_native_record(old,environment)
        environment=project.rsplit('-',1)[1]
        cleanup.validate_native_record(native_record,environment)
        if history[environment][-1] != native_record:
            history[environment].append(native_record)
    if record is None and native_record is None:
        raise ValueError('No completed release to record')
    if record is not None and (not ledger['projects'][project] or ledger['projects'][project][-1] != record):
        ledger['projects'][project].append(record)
    save_ledger(state_dir,ledger)


def bootstrap_native_history(state_dir, history):
    path=state_dir/'rollback-ledger.json';ledger=json.loads(path.read_text())
    if ledger.get('version') != 1 or set(ledger.get('projects',{})) != set(cleanup.PROJECTS):
        raise ValueError('Require existing audited application ledger')
    if 'nativeProjects' in ledger:
        raise ValueError('Native history already exists; bootstrap must not replace it')
    refs, _, _ = cleanup.native_pins(history)
    available={ref for item in cleanup.inspect_all('image') for ref in (item.get('RepoTags') or [])+(item.get('RepoDigests') or [])}
    if refs-available: raise ValueError('Audited native rollback images are not all available locally')
    ledger['nativeProjects']=history
    save_ledger(state_dir,ledger)


def verify_current(project, record, containers):
    current=cleanup.current_release(project,containers)
    if current['images'] != record['images']:
        raise ValueError('Declared release is not the current healthy application bundle')
    if cleanup.product(project)=='octospark':
        environment=project.rsplit('-',1)[1]
        proof=json.loads((cleanup.NATIVE/('release-'+environment+'.json')).read_text())
        if (proof.get('environment')!=environment or proof.get('canaryPassed') is not True
                or proof.get('rendererImage') != record['images']['RENDERER_IMAGE_AMD64']
                or proof.get('mediaImage') != record['images']['MEDIA_RUNTIME_IMAGE_AMD64']):
            raise ValueError('Native canary proof does not match the completed release')
    available={ref for item in cleanup.inspect_all('image') for ref in (item.get('RepoTags') or [])+(item.get('RepoDigests') or [])}
    if set(record['images'].values()) - available:
        raise ValueError('Completed release images are not all available locally')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project',choices=cleanup.PROJECTS)
    parser.add_argument('--artifact')
    parser.add_argument('--native-only',action='store_true')
    parser.add_argument('--bootstrap-native-history',type=Path)
    parser.add_argument('--evidence')
    parser.add_argument('--state-dir',type=Path,default=Path('/opt/host-docker-cleanup/state'))
    args=parser.parse_args()
    if args.bootstrap_native_history:
        if args.project or args.artifact or args.evidence or args.native_only:
            parser.error('Bootstrap is a separate operator action')
    else:
        if not args.project or not args.evidence or (not args.native_only and not args.artifact):
            parser.error('Project, evidence and application artifact or native-only mode required')
        validate_inputs(args.project,args.artifact,args.evidence,args.native_only)
    with ExitStack() as stack:
        for path in [args.state_dir/'hetzner-images.lock',cleanup.NATIVE/'deploy.lock']:
            handle=stack.enter_context(path.open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.bootstrap_native_history:
            bootstrap_native_history(args.state_dir,json.loads(args.bootstrap_native_history.read_text()))
            print('Bootstrapped audited native rollback history; application history unchanged')
            return
        record=None
        if not args.native_only:
            record=cleanup.read_artifact(args.artifact,cleanup.product(args.project))
            record['evidence']=args.evidence
            verify_current(args.project,record,cleanup.inspect_all('container'))
        native=None
        if cleanup.product(args.project)=='octospark':
            native=cleanup.current_native_record(args.project.rsplit('-',1)[1],args.evidence)
            available={ref for item in cleanup.inspect_all('image') for ref in (item.get('RepoTags') or [])+(item.get('RepoDigests') or [])}
            if cleanup.native_record_refs(native)-available:
                raise ValueError('Completed native images are not all available locally')
        append_receipt(args.state_dir,args.project,record,native)
        print('Recorded verified release receipt for '+args.project)


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
        raise SystemExit('Release receipt refused: '+str(error))
