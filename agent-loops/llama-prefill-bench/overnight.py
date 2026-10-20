#!/usr/bin/env python3
"""Finite Studio benchmark coordinator and independent launchd recovery watcher."""
import argparse
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import random
import signal
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = Path(__file__).resolve().parent
loader = importlib.util.spec_from_file_location('window', HERE / 'run-window.py')
window = importlib.util.module_from_spec(loader)
loader.loader.exec_module(window)


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + f'.{os.getpid()}.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def utc():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def epoch(value):
    return dt.datetime.fromisoformat(value).timestamp()


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def live():
    result = {}
    for name, url in [('native', 'http://127.0.0.1:8082/health'),
                      ('gateway', 'http://100.77.197.1:8080/v1/models'),
                      ('temporary', 'http://127.0.0.1:8083/health')]:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                result[name] = json.load(response)
        except Exception as error:
            result[name] = {'unavailable': str(error)}
    return result


def healthy(snapshot):
    return snapshot['native'].get('status') == 'ok' and any(
        m.get('id') == 'qwen3.8-flash-next' for m in snapshot['gateway'].get('data', []))


def samples(base):
    rows = []
    for path in sorted((base / 'results').glob('*/workload/*.stdout')):
        for line in path.read_text(errors='replace').splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict) and 'avg_ts' in row and 'n_prompt' in row:
                row['_source'] = str(path.relative_to(base))
                spec=load(base/'state/specs'/(path.parent.parent.name+'.json'), {})
                row['_requested_repetitions']=spec.get('repetitions',2)
                rows.append(row)
    return rows


def candidate(base):
    rows = [r for r in samples(base) if 'gpu-table' not in r['_source']]
    groups = {}
    for row in rows:
        key = (row.get('n_batch'), row.get('n_ubatch'))
        groups.setdefault(key, {'pp': [], 'tg': []})
        if row['n_prompt'] == 32768:
            groups[key]['pp'].append(row['avg_ts'])
        if row['n_prompt'] == 0 and row.get('n_gen') == 128:
            groups[key]['tg'].append(row['avg_ts'])
    baseline = groups.get((2048, 512), {})
    base_tg = statistics.median(baseline.get('tg') or [0])
    base_pp = statistics.median(baseline.get('pp') or [0])
    choices = [(statistics.median(v['pp']), key) for key, v in groups.items()
               if v['pp'] and v['tg'] and statistics.median(v['tg']) >= base_tg * .95]
    best = max(choices, default=(0, (2048, 512)))
    # Prefer the known baseline unless the observed gain exceeds a 5% hurdle.
    batch, ubatch = best[1] if best[0] > base_pp * 1.05 else (2048, 512)
    cpu = True
    gpu = [r for r in samples(base) if '/gpu-table.stdout' in r['_source']]
    control = [r for r in samples(base) if '/cpu-control.stdout' in r['_source']]
    def speed(data, prompt, gen):
        return statistics.median([r['avg_ts'] for r in data if r['n_prompt']==prompt and r.get('n_gen')==gen] or [0])
    gpu_server = load(base/'results/gpu-server-01/restored.json', {})
    if (gpu_server and not gpu_server.get('failure') and
        speed(gpu,32768,0) > speed(control,32768,0)*1.05 and
        speed(gpu,0,128) >= speed(control,0,128)*.95):
        cpu = False
    checkpoint=8192
    def structured_middle(spacing):
        paths=list((base/'results'/f'structured-{spacing}'/'workload').glob('*-structured/summary.jsonl'))
        if not paths: return None
        rows=[json.loads(line) for line in paths[0].read_text().splitlines()]
        return next((r for r in rows if r['label']=='structured-middle-A'), None)
    old,new=structured_middle(8192),structured_middle(2048)
    if old and new and new['ttft_s'] < old['ttft_s']*.9 and new['timings']['cache_n'] > old['timings']['cache_n']:
        checkpoint=2048
    return {'kind': 'server', 'name': 'candidate-full-context', 'batch': batch,
            'ubatch': ubatch, 'cache': 8192, 'cpu': cpu, 'checkpoint':checkpoint,
            'selection': {'baseline_pp32768': base_pp, 'best_observed_pp32768': best[0],
                          'decode_floor': base_tg * .95, 'requires_full_server_validation': True}}


def report(base, state, manifest):
    current = load(state / 'progress.json', {})
    lines = ['# Overnight llama.cpp benchmark results', '',
        'Status: ' + current.get('phase', 'starting') + '. Updated ' + utc() + '.', '',
        'Authorised at ' + manifest['authorised_at'] + '; immutable recovery-inclusive hard end ' +
        manifest['hard_end'] + '. No permanent production configuration changes are authorised.', '',
        'Environment: Mac Studio M4 Max, 128 GiB; llama.cpp 0.7.0 b11669 / 620d2225c; '
        'q8_0 K/V, Metal flash attention; model Qwen3.8-Flash-Next UD-Q3_K_XL. '
        'Raw evidence is under results/ and state/hourly/.', '',
        '## Native cache measurements', '',
        '| Window | Request | Input | prompt_n | cache_n | TTFT s | Prefill t/s | Decode t/s |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for path in sorted((base / 'results').glob('*/workload/*-cache/summary.jsonl')):
        for line in path.read_text().splitlines():
            r = json.loads(line); t = r['timings']
            lines.append(f"| {path.parts[-4]} | {r['label']} | {r['input_tokens']} | {t.get('prompt_n')} | {t.get('cache_n')} | {r['ttft_s']:.3f} | {t['prompt_per_second']:.1f} | {t['predicted_per_second']:.1f} |")
    lines += ['', 'Native token arrays do not contain structured chat message boundaries. '
        'Their middle-edit replay cannot establish pi chat checkpoint behaviour. '
        'tokens_cached is retained state, not the number of reused prompt tokens; use timings.cache_n.', '',
        '## Structured chat checkpoints', '',
        '| Window | Request | prompt_n | cache_n | TTFT s | First visible s | Decode t/s |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: |']
    for path in sorted((base / 'results').glob('*/workload/*-structured/summary.jsonl')):
        for line in path.read_text().splitlines():
            r = json.loads(line); t = r['timings']
            lines.append(f"| {path.parts[-4]} | {r['label']} | {t.get('prompt_n')} | {t.get('cache_n')} | {r['ttft_s']:.3f} | {r.get('first_visible_s')} | {t['predicted_per_second']:.1f} |")
    lines += ['', '## Chat API pair measurements', '',
        '| Window | Turn | prompt_n | cache_n | TTFT s | First visible s |',
        '| --- | ---: | ---: | ---: | ---: | ---: |']
    for path in sorted((base / 'results').glob('*/workload/*-chat/turn*.summary.json')):
        r=load(path); t=r.get('timings') or {}
        lines.append(f"| {path.parts[-4]} | {r['turn']} | {t.get('prompt_n')} | {t.get('cache_n')} | {r['ttft_s']:.3f} | {r.get('first_visible_s')} |")
    lines += ['', 'A null first-visible value means no visible answer before the output limit. '
        'Generated reasoning counts toward TTFT but does not prove a visible response.']
    lines += ['', '## Raw llama-bench results', '',
        'Each row links its output file. avg_ts is the mean of the requested internal repetitions; '
        'stddev_ts is preserved in raw JSONL. These single-sequence benchmark contexts do not '
        'establish capacity for two 131072-token server slots.', '',
        '| Source | b | ub | pp | tg | repetitions | Mean t/s | Stddev t/s |',
        '| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in samples(base):
        lines.append(f"| [{Path(r['_source']).stem}]({r['_source']}) | {r.get('n_batch')} | {r.get('n_ubatch')} | {r['n_prompt']} | {r.get('n_gen')} | {r['_requested_repetitions']} | {r['avg_ts']:.2f} | {r.get('stddev_ts', 0):.2f} |")
    lines += ['', '## Windows and restoration', '',
        '| Window | Status / abort reason | Outage seconds | Plist unchanged |',
        '| --- | --- | ---: | --- |']
    for path in sorted((base / 'results').glob('*/restored.json')):
        r = load(path)
        lines.append(f"| {path.parent.name} | {r.get('failure') or 'Completed; native health and gateway alias confirmed'} | {r['outage_seconds']:.1f} | {r['plist_unchanged']} |")
    resources = []
    for path in sorted((base / 'results').glob('*/resources.jsonl')):
        resources += [json.loads(line) for line in path.read_text().splitlines()]
    if resources:
        lines += ['', f"Minimum sampled memory-pressure free indicator: {min(r['pressure_free_pct'] for r in resources)}%. "
            f"Minimum disk: {min(r['disk_free_bytes'] for r in resources)/2**30:.2f} GiB. "
            'Every raw swap sample is retained; any nonzero swap stops the run.']
    chosen = load(state / 'candidate.json')
    lines += ['', '## Recommendation status', '']
    if chosen:
        lines += [f"Measured candidate: b={chosen['batch']}, ub={chosen['ubatch']}, CPU table={chosen['cpu']}, checkpoint spacing={chosen.get('checkpoint',8192)}, cache RAM 8192 MiB. "
            'Selection requires >5% prefill gain and a decode floor within 5% of baseline. '
            'This is a candidate until full-server confirmation is reviewed.']
    lines += ['Keep production unchanged until review of final repetitions, full-context logs and restoration evidence. '
        'An 8-to-32 GiB cache increase needs measured retention benefit. GPU placement and thread '
        'comparisons are reported independently; their gains are not assumed to combine.', '',
        'Raw request IDs, SSE events, commands, resource samples, original plist hashes and restored.json '
        'are the evidence. A controller process or submission receipt alone is not a completed benchmark.', '']
    temp = base / 'RESULTS.md.tmp'; temp.write_text('\n'.join(lines)); temp.replace(base / 'RESULTS.md')


def safe_resources():
    try:
        return window.resources()
    except Exception as error:
        return {'stop_condition': str(error)}


def emit_hourly(base, state, manifest, force=False):
    now = time.time(); elapsed = now - epoch(manifest['authorised_at'])
    hour = int(elapsed // 3600)
    if not force and (hour < 1 or (state / 'hourly' / f'hour-{hour}.json').exists()):
        # A coordinator loaded before a source refresh may already have emitted
        # this hour's JSON. The independent watcher still preserves its results.
        archive=state/'hourly'/f'hour-{hour}.RESULTS.md'
        if hour>=1 and not archive.exists() and (base/'RESULTS.md').exists():
            try:
                with archive.open('x') as handle: handle.write((base/'RESULTS.md').read_text())
            except FileExistsError:
                pass
        return
    try:
        resource = window.resources()
    except Exception as error:
        resource = {'error': str(error)}
    value = {'time_utc': utc(), 'elapsed_hours': elapsed/3600, 'hard_end': manifest['hard_end'],
             'progress': load(state/'progress.json', {}), 'resources': resource, 'health': live(),
             'restorations': [str(p.relative_to(base)) for p in sorted((base/'results').glob('*/restored.json'))]}
    report_name = f'final-{time.time_ns()}.json' if force else f'hour-{hour}.json'
    atomic(state/'hourly'/report_name, value)
    atomic(state/'latest-check.json', value)
    report(base, state, manifest)
    archive=(state/'hourly'/report_name).with_suffix('.RESULTS.md')
    archive.write_text((base/'RESULTS.md').read_text())
    print('HOURLY', json.dumps(value), flush=True)


def pid_command(pid):
    return subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='],
                          text=True, capture_output=True, timeout=5).stdout.strip()


def emergency_restore(base, state, manifest, reason):
    progress = load(state/'progress.json', {})
    pid = progress.get('window_pid')
    if pid and 'run-window.py' in pid_command(pid):
        os.kill(pid, signal.SIGTERM)
        until = min(time.time() + 210, epoch(manifest['hard_end']))
        while time.time() < until and pid_command(pid):
            time.sleep(2)
    if healthy(live()):
        atomic(state/'recovery.json', {'at': utc(), 'reason': reason, 'health': live()})
        return
    output = base/'results'/progress.get('window_name', '')
    worker = load(output/'worker.json')
    if worker:
        listing = subprocess.run(['/bin/ps', '-axo', 'pid,pgid,command'], text=True,
                                 capture_output=True, check=True, timeout=5).stdout
        members = [line.split(None, 2) for line in listing.splitlines()[1:]
                   if len(line.split(None, 2)) == 3 and line.split(None, 2)[1] == str(worker['pgid'])]
        allowed = ('workload.py', 'cache-probe.py', 'chat-probe.py', 'structured-probe.py')
        for _, _, command in members:
            if not (any(name in command and str(HERE) in command for name in allowed) or
                    ('llama-server ' in command and '--port 8083' in command)):
                raise RuntimeError('Unknown process in owned group; manual recovery required')
        if members:
            os.killpg(worker['pgid'], signal.SIGTERM); time.sleep(5)
            try: os.killpg(worker['pgid'], signal.SIGKILL)
            except ProcessLookupError: pass
    listeners = window.capture(['/usr/sbin/lsof', '-nP', '-iTCP:8083', '-sTCP:LISTEN'], check=False)
    if listeners.strip():
        raise RuntimeError('Temporary model still listening; do not overlap models')
    loaded = subprocess.run(['/bin/launchctl', 'print', window.JOB], capture_output=True, timeout=5).returncode == 0
    if not loaded:
        window.capture(['/bin/launchctl', 'bootstrap', window.DOMAIN, str(window.PLIST)])
    until = min(time.time() + 180, epoch(manifest['hard_end']))
    while time.time() < until:
        snapshot = live()
        if healthy(snapshot):
            atomic(state/'recovery.json', {'at': utc(), 'reason': reason, 'health': snapshot})
            return
        time.sleep(2)
    raise RuntimeError('Production recovery health/alias failed')


def execute(base, state, manifest, session, name, spec=None, seconds=600):
    hard_end = epoch(manifest['hard_end'])
    if time.time() + seconds + 240 > hard_end:
        progress=load(state/'progress.json', {}); progress.setdefault('skipped', []).append(name+' (insufficient recovery budget)'); atomic(state/'progress.json', progress)
        return False
    if (state/'STOP').exists():
        raise RuntimeError('Persistent STOP marker prevents another window')
    window.resources()
    if not healthy(live()):
        raise RuntimeError('Production not healthy before next window')
    output = base/'results'/name
    if (output/'restored.json').exists():
        return True
    if output.exists():
        raise RuntimeError('Uncertain existing window: ' + name)
    env = os.environ.copy()
    env['LLAMA_WINDOW_SECONDS'] = str(seconds)
    env['LLAMA_HARD_END'] = manifest['hard_end']
    if spec:
        spec_path = state/'specs'/(name+'.json'); atomic(spec_path, spec)
        env['LLAMA_BENCH_SPEC'] = str(spec_path)
    command = [sys.executable, '-B', str(HERE/'run-window.py'), session,
               '--confirm-pause', session, '--out', str(output)]
    with (state/(name+'.controller.log')).open('w') as log:
        process = subprocess.Popen(command, env=env, stdout=log, stderr=subprocess.STDOUT)
        progress = load(state/'progress.json', {})
        progress.update(phase='running', window_name=name, window_pid=process.pid,
                        active_spec=spec or session, heartbeat_utc=utc(), main_pid=os.getpid())
        atomic(state/'progress.json', progress)
        while process.poll() is None:
            progress['heartbeat_utc'] = utc(); atomic(state/'progress.json', progress)
            emit_hourly(base, state, manifest)
            if time.time() > hard_end - 240 or (state/'STOP').exists():
                process.terminate()
            time.sleep(5)
    restored = load(output/'restored.json')
    if not restored or not healthy(live()) or not restored['plist_unchanged']:
        raise RuntimeError('Missing or invalid restoration evidence for ' + name)
    window.resources()  # Any pressure/swap/disk breach prevents another window.
    progress['completed'] = progress.get('completed', []) + [name]
    progress['window_pid'] = None; progress['active_spec'] = None
    progress['last_restored'] = restored
    atomic(state/'progress.json', progress)
    report(base, state, manifest)
    if process.returncode:
        # A GPU allocation failure is a measured failed configuration, but stop limits
        # and unproven recovery always stop the entire study.
        failure = restored.get('failure') or ''
        if session not in ('gpu', 'gpu-server') or 'Stop threshold:' in failure:
            raise RuntimeError('Window failed: ' + name + ': ' + failure)
    return True


def run(base, state, manifest):
    lock = (state/'run.lock').open('a'); fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    existing = load(state/'progress.json', {})
    if existing.get('phase') in ('complete', 'stopped'):
        return
    if time.time() > epoch(manifest['hard_end']) - 300:
        emergency_restore(base, state, manifest, 'immutable deadline on startup'); return
    atomic(state/'progress.json', {'phase': 'waiting-for-first-baseline', 'main_pid': os.getpid(), 'completed': []})
    failure = None
    def interrupted(signum, frame):
        raise RuntimeError('Coordinator signal ' + str(signum))
    signal.signal(signal.SIGTERM, interrupted)
    signal.signal(signal.SIGINT, interrupted)
    try:
        first = Path(manifest.get('baseline_path', str(base/'results/cache8-01/restored.json')))
        while not first.exists():
            if time.time() > epoch(manifest['authorised_at']) + 1000:
                raise RuntimeError('First baseline did not restore within its allowance')
            time.sleep(5)
        baseline = load(first)
        if baseline.get('failure') or not baseline.get('plist_unchanged'):
            raise RuntimeError('First baseline failed')
        if not healthy(live()):
            raise RuntimeError('First baseline health/alias not confirmed')
        progress=load(state/'progress.json'); progress['completed']=['cache8-01']; atomic(state/'progress.json',progress)
        if manifest.get('experiment')=='cache-pressure':
            primary=load(Path(manifest['primary_progress']))
            if not primary or primary.get('phase')!='complete' or primary.get('failure'):
                raise RuntimeError('Cache pressure follow-on requires successful terminal primary study')
            # No override keeps this INPUT tensor on CPU in this llama.cpp build.
            # Repeat the raw comparison with an explicit, verified MTL0 override.
            execute(base,state,manifest,'gpu','explicit-placement-bench',seconds=840)
            chosen=load(Path(manifest['primary_progress']).parent/'candidate.json')
            raw=samples(base)
            def rate(label,prompt,gen):
                return statistics.median([r['avg_ts'] for r in raw if r['_source'].endswith('/'+label+'.stdout') and r['n_prompt']==prompt and r.get('n_gen')==gen] or [0])
            baseline_pp=rate('cpu-control',32768,0); baseline_tg=rate('cpu-control',0,128)
            eligible=[label for label in ('cpu-resident','gpu-table') if rate(label,32768,0)>baseline_pp*1.05 and rate(label,0,128)>=baseline_tg*.95]
            placement=None
            if chosen and eligible:
                best=max(eligible,key=lambda label:rate(label,32768,0))
                placement=dict(chosen,name='resident-combined',cpu=best=='cpu-resident',lazy_mode='off')
            if rate('gpu-table',32768,0)>0:
                execute(base,state,manifest,'custom','placement-proof',
                    {'kind':'bench','name':'placement-proof','batch':2048,'ubatch':512,
                     'lengths':'512','repetitions':1,'cpu':False,'verbose':True},seconds=180)
            churn=int(manifest.get('pressure_churn_count',12))
            ceiling=int(manifest.get('pressure_window_seconds',1500))
            if time.time()+2*(ceiling+240) > epoch(manifest['hard_end']):
                progress=load(state/'progress.json'); progress.setdefault('skipped',[]).append('cache pressure pair: insufficient original recovery-inclusive budget'); atomic(state/'progress.json',progress)
            else:
                for cache in (8192,32768):
                    name=f'pressure-cache{cache}'
                    execute(base,state,manifest,'custom',name,
                        {'kind':'server','name':name,'cache':cache,'probe_lengths':'32768','churn_count':churn},seconds=ceiling)
            if placement:
                execute(base,state,manifest,'custom','resident-combined',placement,seconds=720)
            return
        for session in ('cache32', 'batch-short', 'long2048', 'long4096', 'gpu', 'threads'):
            execute(base, state, manifest, session, session+'-01', seconds=840 if session in ('long2048','long4096','gpu') else 600)
        # Verify full production context if raw all-GPU allocation succeeded.
        gpu_restore = load(base/'results/gpu-01/restored.json', {})
        if not gpu_restore.get('failure'):
            execute(base, state, manifest, 'gpu-server', 'gpu-server-01')
        for spacing in (8192, 2048):
            execute(base, state, manifest, 'custom', f'structured-{spacing}',
                    {'kind':'server', 'name':f'checkpoint{spacing}', 'structured':True, 'checkpoint':spacing}, seconds=720)
        # Counterbalanced repeat rounds across all six CPU-table batch combinations.
        # Every measured command has five internal repetitions, plus full warm-up.
        configs = [(b,u) for b in (2048,4096) for u in (512,1024,2048)]
        for round_index in range(8):
            if time.time() - epoch(manifest['authorised_at']) >= 3.8 * 3600:
                break
            order = configs.copy(); random.Random(20261020+round_index).shuffle(order)
            for batch, ubatch in order:
                if time.time() + 780 + 240 + 2100 > epoch(manifest['hard_end']):
                    break
                name = f'repeat{round_index+1}-b{batch}-ub{ubatch}'
                execute(base, state, manifest, 'custom', name,
                        {'kind':'bench','name':name,'batch':batch,'ubatch':ubatch,'repetitions':5}, seconds=780)
        selected = candidate(base); atomic(state/'candidate.json', selected)
        # Same full-context workload for late baseline and candidate, then native chat.
        execute(base, state, manifest, 'cache8', 'final-baseline', seconds=720)
        execute(base, state, manifest, 'custom', 'final-candidate', selected, seconds=720)
        structured = dict(selected, name='candidate-structured', structured=True)
        execute(base, state, manifest, 'custom', 'final-structured', structured, seconds=720)
    except BaseException as error:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        failure = str(error)
        print('STOP OVERNIGHT:', failure, flush=True)
        emergency_restore(base, state, manifest, failure)
    finally:
        if not healthy(live()):
            emergency_restore(base, state, manifest, failure or 'final verification')
        final_resources=safe_resources()
        if 'stop_condition' in final_resources:
            failure=failure or final_resources['stop_condition']
        progress = load(state/'progress.json', {})
        progress.update(phase='stopped' if failure else 'complete', failure=failure,
                        finished_utc=utc(), main_pid=None, window_pid=None,
                        final_health=live(), final_resources=final_resources)
        atomic(state/'progress.json', progress)
        emit_hourly(base, state, manifest, force=True)
        report(base, state, manifest)


def watch(base, state, manifest):
    progress = load(state/'progress.json', {})
    if progress.get('phase') in ('complete', 'stopped'):
        if progress.get('finished_utc') and time.time()-epoch(progress['finished_utc']) < 30:
            return
        if not healthy(live()):
            emergency_restore(base, state, manifest, 'terminal health check')
        emit_hourly(base, state, manifest, force=True)
        # Remove only this finite job's stopped launchd registrations. Retain code,
        # raw results, reports, original hashes and checkpoints awaiting review.
        removed=[]
        for mode in ('run','watch'):
            label=manifest['labels'][mode]
            plist=Path.home()/'Library/LaunchAgents'/(label+'.plist')
            if plist.exists():
                plist.unlink(); removed.append(str(plist))
        atomic(state/'cleanup.json', {'time':utc(), 'removed':removed,
            'retained':[str(base),str(HERE)], 'reason':'results and source awaiting review'})
        (state/'run.lock').unlink(missing_ok=True)
        for mode in ('run','watch'):
            subprocess.run(['/bin/launchctl','bootout',window.DOMAIN+'/'+manifest['labels'][mode]],
                           stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)
        return
    main_pid=progress.get('main_pid')
    expired=time.time() >= epoch(manifest['hard_end'])-240
    crashed=progress.get('phase')=='running' and main_pid and 'overnight.py' not in pid_command(main_pid)
    if expired or crashed:
        reason='hard-end recovery reserve' if expired else 'coordinator vanished'
        (state/'STOP').write_text(reason)
        emergency_restore(base,state,manifest,reason)
        progress.update(phase='stopped', failure=reason, finished_utc=utc())
        atomic(state/'progress.json',progress)
    emit_hourly(base,state,manifest)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('run','watch','report'))
    parser.add_argument('--manifest',type=Path,required=True)
    args=parser.parse_args(); manifest=load(args.manifest)
    base=Path(manifest['base']); state=base/'state'; state.mkdir(parents=True,exist_ok=True)
    if Path.home()!=Path('/Users/jamesphoenix'):
        raise RuntimeError('Studio only')
    if args.mode=='run': run(base,state,manifest)
    elif args.mode=='watch': watch(base,state,manifest)
    else: report(base,state,manifest)


if __name__=='__main__': main()
