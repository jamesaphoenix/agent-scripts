#!/usr/bin/env python3
"""Resume the frozen Smart Cub render workflow without an agent or provider calls."""
import argparse
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import time


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + f'.{os.getpid()}.tmp')
    with temp.open('w') as handle:
        json.dump(data, handle, indent=2)
        handle.write('\n')
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def environment():
    # No OAuth, provider keys, 1Password token, startup files or API access needed.
    allowed = ('HOME', 'USER', 'LOGNAME', 'TMPDIR', 'LANG')
    env = {key: os.environ[key] for key in allowed if key in os.environ}
    env.update(PATH='/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin',
               PYTHONUNBUFFERED='1', NODE_OPTIONS='--max-old-space-size=8192')
    return env


def birth(pid):
    p = subprocess.run(['ps', '-p', str(pid), '-o', 'stat=', '-o', 'lstart='], capture_output=True, text=True)
    fields = p.stdout.strip().split(None, 1)
    return fields[1].strip() if p.returncode == 0 and len(fields) == 2 and not fields[0].startswith('Z') else None


def stop_owned(record):
    pid = record.get('pid')
    identity = record.get('birth')
    if not pid or not identity or birth(pid) != identity:
        return
    try:
        if os.getpgid(pid) != pid:
            raise RuntimeError(f'Worker no longer owns its recorded process group: {pid}')
        os.killpg(pid, signal.SIGTERM)
        for _ in range(30):
            if birth(pid) != identity:
                return
            time.sleep(0.1)
        if birth(pid) == identity:
            os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def recover_partial(workflow, destination):
    """Quarantine only uncommitted outputs after all earlier workers are stopped."""
    workflow, destination = Path(workflow), Path(destination)
    progress = workflow / 'teaching-join-progress.json'
    committed = {x['output'] for x in read(progress).get('jobs', {}).values()} if progress.exists() else set()
    candidates = []
    for path in (workflow / 'teaching-successor').glob('*.mp4'):
        if not path.with_suffix('.receipt.json').exists():
            if str(path) in committed:
                raise RuntimeError(f'Committed output lost its receipt: {path}')
            candidates.append(path)
    for path in (workflow / 'whole-export-scans').glob('*'):
        if path.is_dir() and re.fullmatch(r'\d{4}-[0-9a-f]{12}', path.name) and not (path / 'report.json').exists():
            candidates.append(path)
    marketing = workflow / 'marketing-render-progress.json'
    marketing_state = read(marketing) if marketing.exists() else None
    reset_roots = []
    if marketing_state:
        for key, record in marketing_state.get('roots', {}).items():
            if record['state'] not in ('preparing', 'rendering'):
                continue
            target = Path(record['target'])
            if not target.resolve().is_relative_to((workflow / 'source').resolve()) or target.name in ('alice', 'james'):
                raise RuntimeError(f'Unexpected interrupted marketing clone: {target}')
            if any(Path(row['output']).is_relative_to(target) for row in marketing_state['outputs'].values()):
                raise RuntimeError(f'Interrupted clone has committed proofs; inspection required: {target}')
            if target.exists():
                candidates.append(target)
            reset_roots.append(key)
    moved = []
    for path in candidates:
        if path.is_symlink() or not path.resolve().is_relative_to(workflow.resolve()):
            raise RuntimeError(f'Unexpected partial-output path: {path}')
        target = destination / path.relative_to(workflow)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))
        moved.append({'from': str(path), 'to': str(target)})
    if reset_roots:
        atomic(destination / 'marketing-progress-before-recovery.json', marketing_state)
        for key in reset_roots:
            del marketing_state['roots'][key]
        atomic(marketing, marketing_state)
    return moved


def export_candidates(config):
    w = Path(config['workflow'])
    impact = read(w / 'impact-map-v3.json')['outputs']
    baseline = read(config['baselineManifest'])
    old = {(r['kind'], r['id'], r['aspect']): r for r in baseline['videos']}
    marketing = read(w / 'marketing-render-progress.json')['outputs']
    teaching = read(w / 'teaching-join-progress.json')['jobs']
    ctas = {r['id']: r for r in read(w / 'source/james/data/ctas.json')}
    output = w / 'rendered-videos'
    rows = []
    if len(impact) != config['total']:
        raise RuntimeError('Selection count changed')
    for row in impact:
        key = (row['kind'], row['id'], row['aspect'])
        original = old[key]
        relative = Path(original['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise RuntimeError('Export path must remain inside the candidate folder')
        if not row['affected']:
            source, digest = Path(row['currentPath']), row['currentSha256']
        elif row['kind'] == 'marketing':
            proof = marketing[f"{row['id']}|{row['aspect']}"]
            source, digest = Path(proof['output']), proof['outputSha256']
        elif row['kind'] == 'teaching':
            proof = teaching[f"{row['id'].split(':', 1)[1]}|{row['aspect']}"]
            source, digest = Path(proof['output']), proof['outputSha256']
        else:
            source = w / 'source/james/out' / f"{row['id']}-{row['aspect']}.mp4"
            digest = read(source.with_suffix('.render-receipt.json'))['output']['sha256']
        if sha(source) != digest:
            raise RuntimeError(f'Rendered output identity changed: {source}')
        target = output / original['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise RuntimeError(f'Unexpected export symlink: {target}')
        if target.exists():
            if sha(target) != digest:
                raise RuntimeError(f'Existing export differs: {target}')
        else:
            os.link(source.resolve(), target)
        cta = ctas[row['ctaId']]
        metadata = {k: original[k] for k in ('id', 'kind', 'aspect', 'title', 'audience', 'cta_id',
                    'distribution_restrictions') if k in original}
        rows.append({**metadata, 'path': original['path'], 'sha256': digest,
                     'source_project_path': str(source),
                     'cta_destination': cta.get('destination', original.get('cta_destination')),
                     'review_state': 'pending_changed_output_review' if row['affected'] else 'inherited_exact_bytes',
                     'previous_checks': original.get('checks'), 'checks': {},
                     'review_summary': 'Rendered candidate; no new editorial approval asserted.'})
    result = {'contract': 'smartcub-unapproved-rendered-candidates/v1', 'at': now(),
              'total': len(rows), 'publication_performed': False, 'approved_for_launch': False,
              'previous_generation': baseline['generation'], 'videos': rows,
              'limits': 'Fresh visual/event review remains pending. Current delivery is unchanged.'}
    atomic(output / 'manifest.json', result)
    return str(output)


class Controller:
    def __init__(self, config, tick=10):
        self.config = config
        self.root = Path(config['runtime'])
        self.status_file = self.root / 'status.json'
        self.tick = tick
        self.running = {}
        self.state = {'contract': 'smartcub-standalone-render-status/v1', 'startedAt': now(),
                      'pid': os.getpid(), 'jobs': {}, 'paidRequests': 0, 'approval': 'pending_editorial_review'}

    def validate(self):
        for path, digest in self.config['pins'].items():
            if sha(path) != digest:
                raise RuntimeError(f'Frozen input/helper changed: {path}')
        if str(Path(self.config['current']).resolve()) != self.config['baselineDirectory']:
            raise RuntimeError('Current delivery pointer changed; refusing mixed baselines')

    def save(self, state):
        if state == 'rendering_and_scanning' and any(r['state'] == 'failed' for r in self.state['jobs'].values()):
            state = 'rendering_with_failed_steps'
        self.state.update(state=state, at=now())
        for label, file, key in [('marketing', 'marketing-render-progress.json', 'outputs'),
                                 ('teaching', 'teaching-join-progress.json', 'jobs'),
                                 ('scans', 'whole-export-scans/index.json', 'jobs')]:
            path = Path(self.config['workflow']) / file
            if path.exists():
                data = read(path)
                self.state[label] = (sum(x.get('state', '').startswith('scanned') for x in data[key])
                                     if label == 'scans' else len(data[key]))
        atomic(self.status_file, self.state)
        main_status = Path(self.config['workflow']) / 'status.json'
        if main_status.exists():
            previous = read(main_status)
            previous.update(at=self.state['at'], state='standalone_' + state,
                            executionMode='registered_launchd_no_agent_no_provider_calls',
                            standaloneRunner={'status': str(self.status_file), 'pid': os.getpid(),
                                              'marketing': self.state.get('marketing'),
                                              'teaching': self.state.get('teaching'),
                                              'scans': self.state.get('scans')})
            atomic(main_status, previous)
        print(json.dumps({k: v for k, v in self.state.items() if k != 'jobs'}), flush=True)

    def start(self, name, spec):
        log = (self.root / f'{name}.log').open('a')
        child = subprocess.Popen(spec['argv'], cwd=self.config['workflow'], env=environment(),
                                 stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                 start_new_session=True)
        log.close()
        record = {'state': 'running', 'pid': child.pid, 'birth': birth(child.pid), 'startedAt': now()}
        self.state['jobs'][name] = record
        self.running[name] = child

    def run(self):
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / 'runner.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.validate()
            if self.status_file.exists():
                for record in read(self.status_file).get('jobs', {}).values():
                    if record.get('state') == 'running':
                        stop_owned(record)
            self.state['recoveredPartials'] = recover_partial(
                self.config['workflow'], self.root / 'quarantine' / str(time.time_ns()))
            specs = self.config['steps']
            try:
                while True:
                    failed = {n for n, r in self.state['jobs'].items() if r['state'] == 'failed'}
                    if failed & {'marketing', 'teaching'} and 'scans' in self.running:
                        stop_owned(self.state['jobs']['scans'])
                    for name, spec in specs.items():
                        if name in self.state['jobs']:
                            continue
                        dependencies = spec.get('after', [])
                        if any(self.state['jobs'].get(d, {}).get('state') in ('failed', 'skipped') for d in dependencies):
                            self.state['jobs'][name] = {'state': 'skipped', 'reason': 'dependency_failed'}
                        elif name == 'scans' and failed & {'marketing', 'teaching'}:
                            self.state['jobs'][name] = {'state': 'skipped', 'reason': 'producer_failed'}
                        elif all(self.state['jobs'].get(d, {}).get('state') == 'complete' for d in dependencies):
                            self.start(name, spec)
                    for name, child in list(self.running.items()):
                        code = child.poll()
                        if code is not None:
                            self.state['jobs'][name].update(state='complete' if code == 0 else 'failed',
                                                           exitCode=code, finishedAt=now())
                            self.running.pop(name)
                    if len(self.state['jobs']) == len(specs) and not self.running:
                        success = all(r['state'] == 'complete' for r in self.state['jobs'].values())
                        if success:
                            self.validate()
                            self.save('exporting_candidates')
                            self.state['renderedFolder'] = export_candidates(self.config)
                            self.validate()
                        self.save('rendered_scanned_pending_editorial_review' if success else 'failed_needs_attention')
                        return 0 if success else 1
                    self.save('rendering_and_scanning')
                    time.sleep(self.tick)
            except (KeyboardInterrupt, Exception) as error:
                for name, child in self.running.items():
                    stop_owned(self.state['jobs'][name])
                    child.wait()
                    self.state['jobs'][name].update(state='interrupted', finishedAt=now())
                self.state['error'] = str(error) or type(error).__name__
                self.save('interrupted_resumable' if isinstance(error, KeyboardInterrupt) else 'failed_needs_attention')
                return 0 if isinstance(error, KeyboardInterrupt) else 1


def configure(workflow, runtime):
    w = Path(workflow).resolve()
    if not (w / 'script-handoff.json').is_file():
        raise RuntimeError('Quiescent agent handoff is required')
    current = w.parent.parent / 'delivery/video-delivery/current'
    baseline = current.resolve()
    scripts = ['render-marketing-successors.py', 'join-selected-teaching-successors.py',
               'scan-successor-exact-outputs.py', 'verify-teaching-join-audio.py', 'apply-cta-11plus-source.py']
    paths = [w / name for name in scripts] + [w / 'impact-map-v3.json', baseline / 'manifest.json']
    for root in ['alice', 'james']:
        for pattern in ['src/kit/*.tsx', 'src/kit/*.ts', 'data/ctas.json', 'data/words/cta-sc-*.json',
                        'public/audio/cta-sc-*.wav', 'public/avatar/cta-sc-05.mp4', 'out/cta-sc-*.mp4',
                        'out/cta-sc-*.render-receipt.json']:
            paths.extend((w / 'source' / root).glob(pattern))
    scanner = Path('/Users/jamesphoenix/render-tmp/text-jitter-harness-20261019/source-eof-fix/media-quality/text-jitter/text_jitter.py')
    paths.append(scanner)
    python = '/opt/homebrew/bin/python3'
    steps = {name: {'argv': [python, str(w / script)], 'after': after} for name, script, after in [
        ('marketing', scripts[0], []), ('teaching', scripts[1], []),
        ('scans', scripts[2], []), ('joins', scripts[3], ['teaching'])]}
    data = {'workflow': str(w), 'runtime': str(Path(runtime).resolve()), 'total': 586,
            'baselineDirectory': str(baseline), 'baselineManifest': str(baseline / 'manifest.json'),
            'current': str(current), 'steps': steps, 'pins': {str(p): sha(p) for p in paths}}
    atomic(Path(runtime) / 'config.json', data)
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['configure', 'run', 'status'])
    parser.add_argument('--config', type=Path)
    parser.add_argument('--workflow', type=Path)
    parser.add_argument('--runtime', type=Path)
    args = parser.parse_args()
    if args.command == 'status':
        print(json.dumps(read(read(args.config)['runtime'] + '/status.json'), indent=2))
        return 0
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise RuntimeError('Rendering and configuration run on Mac Studio only')
    if args.command == 'configure':
        data = configure(args.workflow, args.runtime)
        print(json.dumps({'config': str(args.runtime / 'config.json'), 'frozenFiles': len(data['pins'])}))
        return 0
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    controller = Controller(read(args.config))
    try:
        return controller.run()
    except BlockingIOError:
        print('This batch already has an active runner.')
        return 0
    except Exception as error:
        controller.state['error'] = str(error)
        controller.save('failed_needs_attention')
        return 1


if __name__ == '__main__':
    sys.exit(main())
