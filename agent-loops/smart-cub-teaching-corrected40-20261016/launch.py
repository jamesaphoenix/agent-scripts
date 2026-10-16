#!/usr/bin/env python3
"""Argument-only launcher for one separately approved immutable corrected40 plan."""
import hashlib,json,os,re,socket,sys
from pathlib import Path
RUN=Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
PYTHON=RUN/'.venv/bin/python'
CONFIG=Path(__file__).with_name('approved-batch.json')
def sha(path):
 h=hashlib.sha256()
 with path.open('rb')as stream:
  for block in iter(lambda:stream.read(8*1024**2),b''):h.update(block)
 return h.hexdigest()
def pinned(row):
 if set(row)!={'path','bytes','sha256'}:raise ValueError('Exact pinned file reference required')
 p=Path(row['path'])
 if not p.is_absolute()or p.is_symlink()or not p.resolve().is_relative_to(RUN.resolve()):raise ValueError('Pinned path outside task run root')
 if not p.is_file()or p.stat().st_size!=row['bytes']or sha(p)!=row['sha256']:raise ValueError('Pinned bytes changed: '+str(p))
 return p
def command():
 if re.sub('[^a-z0-9]','',socket.gethostname().split('.')[0].lower())!='jamessmacstudio':raise ValueError('Mac Studio required')
 value=json.loads(CONFIG.read_text())
 if set(value)!={'contract','script','plan','status'}or value['contract']!='smart-cub-corrected40-launch/v1':raise ValueError('Exact approved launch binding required')
 script=pinned(value['script']);plan=pinned(value['plan']);status=Path(value['status'])
 if script.name!='blend-corrected-batch.py':raise ValueError('Only the reviewed corrected batch implementation is supported')
 if not status.is_absolute()or not status.resolve().is_relative_to(RUN.resolve())or status.is_symlink():raise ValueError('Status must stay under task run root')
 return[str(PYTHON),'-B',str(script),'run','--plan',str(plan),'--plan-sha256',value['plan']['sha256'],'--status',str(status),'--execute']
def main():
 argv=command()
 if sys.argv[1:]==['--check']:
  print(json.dumps({'state':'pinned_launcher_verified_no_media_or_provider_call','argv':argv}));return
 if sys.argv[1:]!=['--execute']:raise ValueError('Explicit --execute or metadata-only --check required')
 if os.getpriority(os.PRIO_PROCESS,0)!=10:raise ValueError('Exact nice10 policy required')
 os.execv(argv[0],argv)
if __name__=='__main__':main()
