"""Finite, credential-free transfer of archived films from Studio to the MacBook.

No rendering, provider calls, deletion, or paid retries. Offline destinations stay pending.
"""
import argparse
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shlex
import socket
import subprocess
import time

DESTINATION = '/Users/jamesaphoenix/Desktop/JUD-Regenerated-Films-20261018'
TARGET = 'jamesaphoenix@macbook-pro'
END = 1792360800


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def atomic(path, data):
    temporary = path.with_suffix('.sync.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.replace(path)


def collect(run, verified):
    manifest = json.loads((run / 'regeneration-manifest-v1.json').read_text())
    expected = {film['filmGroupId'] for film in manifest['films']}
    if len(expected) != 51:
        raise ValueError('Expected the unchanged 51-film manifest')
    movies, files = [], []
    for film in sorted(expected):
        root = run / 'episodes' / film
        if root.resolve().parent != (run / 'episodes').resolve():
            raise ValueError('Unsafe film path')
        receipt = root / 'archive-receipt.json'
        if not receipt.exists():
            continue
        pair = json.loads(receipt.read_text())
        for aspect in ['portrait', 'landscape']:
            item = pair[aspect]
            relative = f'episodes/{film}/delivery/{aspect}/final.mp4'
            movie = run / relative
            fingerprint = (movie.stat().st_size, movie.stat().st_mtime_ns, item['sha256'])
            if verified.get(relative) != fingerprint:
                if sha(movie) != item['sha256']:
                    raise ValueError(f'Archived movie changed: {relative}')
                verified[relative] = fingerprint
            movies.append({'film': film, 'aspect': aspect, 'file': relative,
                           'sha256': item['sha256'], 'watch': item['watch']})
            files.append(relative)
        files.append(str(receipt.relative_to(run)))
        acceptance = root / 'delivery' / 'acceptance.json'
        if acceptance.exists():
            files.append(str(acceptance.relative_to(run)))
    for name in ['regeneration-manifest-v1.json', 'CONTINUATION-STATUS.json',
                 'COMPLETE-ARCHIVE.json', 'COMPLETE-ARCHIVE-LOCATION.json',
                 'ledger/run2-budget-final.sqlite', 'FINAL-LOCAL-WITNESS.json',
                 'FINAL-LOCAL-REPORT.md', 'LOCAL-REPAIR-STATUS.json', 'TAKEOVER-CLEANUP.json']:
        if (run / name).exists():
            files.append(name)
    return movies, files


REMOTE_VERIFY = r'''
import hashlib, json, pathlib, sys
data=json.load(sys.stdin)
root=pathlib.Path('/Users/jamesaphoenix/Desktop/JUD-Regenerated-Films-20261018')
for movie in data['movies']:
    path=root/movie['file']
    if not path.resolve().is_relative_to(root.resolve()): raise ValueError('Unsafe destination')
    with path.open('rb') as f: actual=hashlib.file_digest(f,'sha256').hexdigest()
    if actual != movie['sha256']: raise ValueError('Destination hash mismatch: '+movie['file'])
tmp=root/'FILMS.sync.tmp';tmp.write_text(json.dumps(data,indent=2)+'\n');tmp.replace(root/'FILMS.json')
lines=['# JUD regenerated films', '', str(len(data['movies'])//2)+' films, '+str(len(data['movies']))+' exports verified here.', '', 'Private candidates awaiting James inspection. Original films and approvals are preserved.', '']
for movie in data['movies']: lines.append('- ['+movie['film']+' '+movie['aspect']+']('+movie['file']+') | [Library]('+movie['watch']+')')
tmp=root/'README.sync.tmp';tmp.write_text('\n'.join(lines)+'\n');tmp.replace(root/'README.md')
print(json.dumps({'verifiedExports':len(data['movies'])}))
'''


def transfer(run, movies, files):
    ssh = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5']
    subprocess.run([*ssh, TARGET, f'mkdir -p {shlex.quote(DESTINATION)}'], check=True, timeout=15, capture_output=True)
    subprocess.run(['rsync', '-rt', '--checksum', '--partial-dir=.rsync-partial',
                    '--timeout=45', '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=5',
                    '--files-from=-', str(run) + '/', f'{TARGET}:{DESTINATION}/'],
                   input='\n'.join(files)+'\n', text=True, check=True, timeout=240, capture_output=True)
    payload = {'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'movies': movies,
               'status': json.loads((run/'CONTINUATION-STATUS.json').read_text())}
    subprocess.run([*ssh, TARGET, '/opt/homebrew/bin/python3 -c ' + shlex.quote(REMOTE_VERIFY)],
                   input=json.dumps(payload), text=True, check=True, timeout=120, capture_output=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    if not socket.gethostname().lower().replace('-', '').startswith('jamessmacstudio'):
        raise SystemExit('This transfer must run on Studio')
    run = args.run.resolve()
    lock = (run/'exports-sync.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    verified = {}
    last_success = None
    while True:
        status = json.loads((run/'CONTINUATION-STATUS.json').read_text())
        report = {'pid': os.getpid(), 'updatedAt': dt.datetime.now(dt.timezone.utc).isoformat(),
                  'destination': f'{TARGET}:{DESTINATION}', 'lastSuccess': last_success,
                  'controllerPhase': status['phase'], 'deletes': False, 'providerCalls': False}
        try:
            movies, files = collect(run, verified)
            report['availableExports'] = len(movies)
            transfer(run, movies, files)
            last_success = report['updatedAt']
            report.update(phase='synced', verifiedExports=len(movies), lastSuccess=last_success)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
            report.update(phase='pending', error=type(error).__name__,
                          note='Originals remain safe on Studio and R2. No provider calls or credential reads.')
        atomic(run/'EXPORT-SYNC-STATUS.json', report)
        print(json.dumps(report), flush=True)
        # Keep offline transfers pending until the original deadline, even if rendering completes first.
        if args.once or time.time() >= END or (status['phase']=='complete' and report['phase']=='synced'
                                               and (run/'FINAL-LOCAL-WITNESS.json').exists()):
            break
        time.sleep(45)


if __name__ == '__main__':
    main()
