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


def validate_inputs(project, artifact, evidence):
    app=cleanup.product(project)
    expected=Path('/opt')/app/'deploy/artifacts'
    path=Path(artifact)
    repo='smart-cub-learning' if app=='trace-learn' else app
    if project not in cleanup.PROJECTS or path.parent != expected or not re.fullmatch(r'images-[a-f0-9]+(?:-amd64|-standby)?\.env',path.name):
        raise ValueError('Invalid release artifact path')
    if not re.fullmatch('https://github.com/Just-Understanding-Data-Ltd/'+repo+r'/actions/runs/[1-9][0-9]*',evidence):
        raise ValueError('Require the owning product release run URL')


def append_receipt(state_dir, project, record):
    path=state_dir/'rollback-ledger.json'
    ledger=json.loads(path.read_text())
    if ledger.get('version') != 1 or set(ledger.get('projects',{})) != set(cleanup.PROJECTS):
        raise ValueError('Require existing audited bootstrap ledger')
    if project in cleanup.retired_projects(ledger):
        raise ValueError('Project is explicitly retired; operator must reactivate it first')
    for name, records in ledger['projects'].items():
        for old in records: cleanup.validate_record(old,cleanup.product(name))
    if ledger['projects'][project][-1] == record: return
    archive=state_dir/'receipt-archive';archive.mkdir(exist_ok=True)
    backup=archive/(cleanup.digest(ledger)+'.json')
    if not backup.exists(): cleanup.atomic_json(backup,ledger)
    ledger['projects'][project].append(record)
    cleanup.atomic_json(path,ledger)


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
    parser.add_argument('--project',required=True,choices=cleanup.PROJECTS)
    parser.add_argument('--artifact',required=True)
    parser.add_argument('--evidence',required=True)
    parser.add_argument('--state-dir',type=Path,default=Path('/opt/host-docker-cleanup/state'))
    args=parser.parse_args()
    validate_inputs(args.project,args.artifact,args.evidence)
    with ExitStack() as stack:
        for path in [args.state_dir/'hetzner-images.lock',cleanup.NATIVE/'deploy.lock']:
            handle=stack.enter_context(path.open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        record=cleanup.read_artifact(args.artifact,cleanup.product(args.project))
        record['evidence']=args.evidence
        verify_current(args.project,record,cleanup.inspect_all('container'))
        append_receipt(args.state_dir,args.project,record)
        print('Recorded verified release receipt for '+args.project+' '+record['sourceSha'])


if __name__=='__main__':
    try:main()
    except (OSError,ValueError,RuntimeError,KeyError,TypeError) as error:
        raise SystemExit('Release receipt refused: '+str(error))
