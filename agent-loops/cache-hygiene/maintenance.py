#!/usr/bin/env python3
"""Cache-only maintenance. Source, sessions, databases and media are out of scope."""
import argparse
import concurrent.futures
import fcntl
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
TEMP_PREFIXES=('remotion-webpack-bundle-','node-compile-cache','tx-agent-kit-vitest-compile-cache','tsx-')
OUTPUT_NAMES=('node_modules','.turbo','.next','.vite','coverage')

def cache_candidate(path, parent, handles, cutoff):
    """Require an owned direct child, no symlink, no live handle, and age."""
    if path.parent!=parent or path.is_symlink() or not path.is_dir():return False
    s=path.stat()
    if s.st_uid!=os.getuid() or max(s.st_mtime,s.st_ctime)>cutoff:return False
    name=str(path).removeprefix('/private').casefold().rstrip('/')
    return not any(h==name or h.startswith(name+'/') for h in handles)

def snapshot():
    p=subprocess.run(['lsof','-Fn'],capture_output=True,text=True,timeout=60)
    handles=[s[1:].removeprefix('/private').casefold() for s in p.stdout.splitlines() if s.startswith('n/')]
    if len(handles)<5:raise RuntimeError('Insufficient live-handle inventory; refusing cleanup')
    commands=subprocess.check_output(['ps','-axo','command='],text=True).splitlines()
    mounts=[];readiness_container=False
    contexts=subprocess.run(['docker','context','ls','--format','{{.Name}}'],capture_output=True,text=True,timeout=20)
    if contexts.returncode:raise RuntimeError('Cannot inventory container contexts')
    for context in ('default','colima-ci'):
        if context not in contexts.stdout.split():continue
        ids=subprocess.run(['docker','--context',context,'ps','-aq'],capture_output=True,text=True,timeout=30)
        if ids.returncode:raise RuntimeError('Cannot inventory Docker '+context)
        if not ids.stdout.strip():continue
        p=subprocess.run(['docker','--context',context,'inspect',*ids.stdout.split()],capture_output=True,text=True,timeout=45,check=True)
        for c in json.loads(p.stdout):
            readiness_container |= c['State']['Running'] and 'mobile-readiness' in c['Name']
            mounts.extend(Path(m['Source'].removeprefix('/host_mnt')) for m in c.get('Mounts',[]) if m['Type']=='bind')
    return handles,commands,mounts,readiness_container

def busy_tree(tree, handles, commands, mounts):
    name=str(tree).casefold()
    return any(h==name or h.startswith(name+'/') for h in handles) or any(name in c.casefold() for c in commands) or any(m==tree or m.is_relative_to(tree) or tree.is_relative_to(m) for m in mounts)

def readiness_busy(commands, container):
    return container or any('mobile-readiness' in c and 'maintenance.py' not in c for c in commands)

def disk_free_bytes():
    raw=subprocess.check_output(['df','-k','/System/Volumes/Data'],text=True,timeout=10)
    return int(raw.splitlines()[-1].split()[3])*1024

def project_dependency_roots(projects):
    """Walk source directories without entering dependency stores or Unity state."""
    for base,dirs,_ in os.walk(projects,followlinks=False):
        for name in list(dirs):
            path=Path(base)/name
            if name=='node_modules':
                dirs.remove(name)
                if not path.is_symlink():yield path
            elif name in {'.git','.venv','venv','Library','Temp','__pycache__'} or path.is_symlink():
                dirs.remove(name)

def git_root(path, boundary):
    while path!=boundary and path.is_relative_to(boundary):
        if (path/'.git').exists():return path
        path=path.parent
    return None

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true',help='Apply the audited cache-only plan; default is dry-run')
    parser.add_argument('--state-dir',type=Path,default=ROOT/'state/cache-hygiene')
    args=parser.parse_args()
    state=args.state_dir;state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (state/'disabled').exists():print('Disabled');return
    lock=(state/'lock').open('a')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:print('Maintenance already running');return
    handles,commands,mounts,container=snapshot()
    now=time.time();rows=[]
    node_jobs=any(('vitest' in c or ('node' in c and '--import tsx' in c)) and 'maintenance.py' not in c for c in commands)
    temp=Path(subprocess.check_output(['getconf','DARWIN_USER_TEMP_DIR'],text=True).strip())
    for parent in (temp,Path('/private/tmp')):
        for child in parent.iterdir():
            if not child.name.startswith(TEMP_PREFIXES):continue
            if node_jobs and not child.name.startswith('remotion-webpack-bundle-'):continue
            if cache_candidate(child,parent,handles,now-86400):rows.append({'path':str(child),'kind':'temp-cache'})
    for project in ('trace-learn','octospark'):
        repo=Path.home()/'Desktop/projects/just-understanding-data'/project
        parent=repo/'.worktrees'
        if not parent.exists():continue
        for tree in parent.iterdir():
            if not tree.is_dir() or tree.is_symlink() or not (tree/'.git').exists():continue
            if busy_tree(tree,handles,commands,mounts):continue
            for name in OUTPUT_NAMES:
                child=tree/name
                if not cache_candidate(child,tree,handles,now-7*86400):continue
                tracked=subprocess.run(['git','-C',str(tree),'ls-files','--',name],capture_output=True,text=True,timeout=30)
                ignored=subprocess.run(['git','-C',str(tree),'check-ignore','-q',name]).returncode==0
                if tracked.returncode==0 and not tracked.stdout and ignored:rows.append({'path':str(child),'tree':str(tree),'kind':'worktree-output'})
    # Main checkouts and nested apps can also accumulate abandoned dependencies.
    projects=Path.home()/'Desktop/projects'
    planned={r['path'] for r in rows}
    if projects.exists():
        for child in project_dependency_roots(projects):
            if str(child) in planned or not cache_candidate(child,child.parent,handles,now-7*86400):continue
            tree=git_root(child.parent,projects)
            if tree is None or busy_tree(tree,handles,commands,mounts):continue
            relative=str(child.relative_to(tree))
            tracked=subprocess.run(['git','-C',str(tree),'ls-files','--',relative],capture_output=True,text=True,timeout=30)
            ignored=subprocess.run(['git','-C',str(tree),'check-ignore','-q',relative]).returncode==0
            if tracked.returncode==0 and not tracked.stdout and ignored:
                rows.append({'path':str(child),'tree':str(tree),'kind':'worktree-output'})
    readiness=Path.home()/'.cache/tracelearn-mobile-readiness/staging'
    if readiness.exists() and not readiness_busy(commands,container):
        current=readiness/'deterministic'
        if current.is_dir() and not current.is_symlink():
            s=current.stat()
            overflow=s.st_size>4*1024*1024
            stale=s.st_mtime<now-7*86400
            if (overflow or stale) and cache_candidate(current,readiness,handles,now-300):
                rows.append({'path':str(current),'kind':'rotate-deterministic','reason':'directory index budget' if overflow else 'seven-day cache expiry'})
        for p in readiness.iterdir():
            if re.fullmatch(r'(?:deterministic|screenshots)\.retired-[0-9]+',p.name) and p.is_dir() and not p.is_symlink() and p.stat().st_uid==os.getuid():rows.append({'path':str(p),'kind':'retired-cache'})
    report={'at':now,'apply':args.apply,'free_bytes_before':disk_free_bytes(),'plan':rows,'results':[]}
    (state/'last-plan.json').write_text(json.dumps(report,indent=2)+'\n')
    for row in rows:
        p=Path(row['path']);result=dict(row)
        if not args.apply:result['result']='dry-run';report['results'].append(result);continue
        # Refresh activity before each mutation. Writers that appear mid-pass
        # cause retention, including the entire readiness namespace.
        fresh,procs,binds,active=snapshot()
        if row['kind'] in ('rotate-deterministic','retired-cache'):
            if readiness_busy(procs,active) or busy_tree(p,fresh,[],binds):
                result['result']='retained_active';report['results'].append(result);continue
            if row['kind']=='rotate-deterministic' and not cache_candidate(p,p.parent,fresh,time.time()-300):
                result['result']='retained_active_or_changed';report['results'].append(result);continue
        elif not cache_candidate(p,p.parent,fresh,time.time()-(7*86400 if row['kind']=='worktree-output' else 86400)):
            result['result']='retained_active_or_changed';report['results'].append(result);continue
        if row.get('tree') and busy_tree(Path(row['tree']),fresh,procs,binds):
            result['result']='retained_active';report['results'].append(result);continue
        if row['kind']=='rotate-deterministic':
            retired=p.with_name('deterministic.retired-'+str(time.time_ns()))
            p.rename(retired);p.mkdir(mode=0o700);p=retired
        if row['kind'] in ('rotate-deterministic','retired-cache'):
            targets=[p]
            screenshots=p/'screenshots'
            if screenshots.is_dir() and not screenshots.is_symlink():
                moved=readiness/('screenshots.retired-'+str(time.time_ns()));screenshots.rename(moved);targets.append(moved)
            def clear(target):
                return subprocess.run([str(HERE/'unlink-cache'),str(target)],timeout=3600).returncode
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                codes=list(pool.map(clear,targets))
            result['result']='removed' if all(c==0 for c in codes) else 'retained_unknown_entries'
        else:shutil.rmtree(p);result['result']='removed'
        report['results'].append(result)
        (state/'last-report.json').write_text(json.dumps(report,indent=2)+'\n')
    report['free_bytes_after']=disk_free_bytes()
    report['disk_warning']=report['free_bytes_after']<150*2**30
    (state/'last-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'apply':args.apply,'targets':len(rows),'free_gib':round(report['free_bytes_after']/2**30,1),'warning':report['disk_warning']}))

if __name__=='__main__':main()
