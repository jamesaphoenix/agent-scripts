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
import stat
import subprocess
import sys
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

def protected_bind(mount, container):
    """A read-only node-exporter host-metrics mount does not own project files."""
    if mount.get('Type')!='bind':return False
    source=mount.get('Source','').removeprefix('/host_mnt') or '/'
    metrics_root=(source=='/' and mount.get('Destination')=='/rootfs' and mount.get('RW') is False and 'node-exporter' in container.get('Config',{}).get('Image',''))
    return not metrics_root

def snapshot():
    def mark(stage):
        target=os.environ.get('CACHE_HYGIENE_PROGRESS')
        if target:Path(target).write_text(json.dumps({'at':time.time(),'stage':stage})+'\n')
    mark('snapshot-lsof')
    p=subprocess.run(['lsof','-nP','-Fn'],capture_output=True,text=True,timeout=60)
    handles=[s[1:].removeprefix('/private').casefold() for s in p.stdout.splitlines() if s.startswith('n/')]
    if len(handles)<5:raise RuntimeError('Insufficient live-handle inventory; refusing cleanup')
    mark('snapshot-ps')
    commands=subprocess.check_output(['ps','-axo','command='],text=True).splitlines()
    mounts=[];readiness_container=False
    mark('snapshot-docker-contexts')
    contexts=subprocess.run(['docker','context','ls','--format','{{.Name}}'],capture_output=True,text=True,timeout=20)
    if contexts.returncode:raise RuntimeError('Cannot inventory container contexts')
    for context in ('default','colima-ci'):
        if context not in contexts.stdout.split():continue
        mark('snapshot-docker-'+context)
        ids=subprocess.run(['docker','--context',context,'ps','-aq'],capture_output=True,text=True,timeout=30)
        if ids.returncode:raise RuntimeError('Cannot inventory Docker '+context)
        if not ids.stdout.strip():continue
        mark('snapshot-inspect-'+context)
        p=subprocess.run(['docker','--context',context,'inspect',*ids.stdout.split()],capture_output=True,text=True,timeout=45,check=True)
        for c in json.loads(p.stdout):
            readiness_container |= c['State']['Running'] and 'mobile-readiness' in c['Name']
            mounts.extend(Path(m['Source'].removeprefix('/host_mnt')) for m in c.get('Mounts',[]) if protected_bind(m,c))
    mark('snapshot-complete')
    return handles,commands,mounts,readiness_container

def busy_tree(tree, handles, commands, mounts):
    name=str(tree).casefold()
    return any(h==name or h.startswith(name+'/') for h in handles) or any(name in c.casefold() for c in commands) or any(m==tree or m.is_relative_to(tree) or tree.is_relative_to(m) for m in mounts)

def readiness_busy(commands, container):
    return container or any('mobile-readiness' in c and 'maintenance.py' not in c for c in commands)

def disk_free_bytes():
    raw=subprocess.check_output(['df','-k','/System/Volumes/Data'],text=True,timeout=10)
    return int(raw.splitlines()[-1].split()[3])*1024

def oversized_service_logs(handles, home=None):
    """Report known service logs using metadata only; never rotate live writers."""
    home=Path.home() if home is None else home
    parent=home/'.cloudflared/logs'
    for directory in (parent.parent,parent):
        try:s=directory.lstat()
        except FileNotFoundError:return {'status':'absent','warnings':[]}
        except OSError as e:return {'status':'unreadable','error':str(e),'warnings':[]}
        if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid():
            return {'status':'retained_untrusted_path','warnings':[]}
    warnings=[]
    try:
        with os.scandir(parent) as entries:
            for count,entry in enumerate(entries):
                if count>=128:return {'status':'partial_entry_limit','warnings':warnings}
                if not entry.name.endswith('.log'):continue
                s=entry.stat(follow_symlinks=False)
                if not stat.S_ISREG(s.st_mode) or s.st_uid!=os.getuid() or s.st_size<256*2**20:continue
                path=Path(entry.path);name=str(path).removeprefix('/private').casefold()
                warnings.append({'path':str(path),'kind':'oversized-service-log','bytes':s.st_size,
                    'modified_at':s.st_mtime,'open_handle':name in handles,
                    'next_step':'Configure controlled service log rotation; retain the active log.'})
    except OSError as e:return {'status':'partial_unreadable','error':str(e),'warnings':warnings}
    return {'status':'complete','warnings':warnings}

def project_dependency_roots(projects):
    """Walk source directories without entering dependency stores or Unity state."""
    for base,dirs,_ in os.walk(projects,followlinks=False):
        for name in list(dirs):
            path=Path(base)/name
            if name in OUTPUT_NAMES:
                dirs.remove(name)
                if not path.is_symlink():yield path
            elif name in {'.git','.venv','venv','Library','Temp','__pycache__','.data','.artifacts'} or path.is_symlink():
                dirs.remove(name)

def bounded_dependency_discovery(projects, state, timeout=60):
    """A stalled filesystem must not prevent all other cache maintenance."""
    output=state/'dependency-discovery.jsonl'
    with output.open('w') as log:
        worker=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--scan-dependencies',str(projects)],stdout=log,stderr=subprocess.PIPE,text=True)
        try:
            _,error=worker.communicate(timeout=timeout)
            status='complete' if worker.returncode==0 else 'failed'
        except subprocess.TimeoutExpired:
            worker.kill();_,error=worker.communicate();status='timed_out'
    paths=[]
    for line in output.read_text().splitlines():
        row=json.loads(line)
        path=Path(row['path'])
        if path.name in OUTPUT_NAMES and path.is_relative_to(projects):paths.append(path)
    return paths,{'status':status,'count':len(paths),'error':error[-2000:]}

def git_root(path, boundary):
    while path.is_relative_to(boundary):
        if (path/'.git').exists():return path
        if path==boundary:break
        path=path.parent
    return None

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--projects-root',type=Path,action='append',help='Dependency scan root; repeat for runtime checkouts. Default Desktop/projects')
    parser.add_argument('--apply',action='store_true',help='Apply the audited cache-only plan; default is dry-run')
    parser.add_argument('--state-dir',type=Path,default=ROOT/'state/cache-hygiene')
    args=parser.parse_args()
    state=args.state_dir;state.mkdir(parents=True,exist_ok=True,mode=0o700)
    global_state=ROOT/'state/cache-hygiene';global_state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if (state/'disabled').exists() or (global_state/'disabled').exists():print('Disabled');return
    with (global_state/'lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('Maintenance already running');return
        def progress(stage,path=None):
            (state/'progress.json').write_text(json.dumps({'at':time.time(),'stage':stage,'path':str(path) if path else None})+'\n')
        os.environ['CACHE_HYGIENE_PROGRESS']=str(state/'progress.json')
        progress('activity-snapshot')
        handles,commands,mounts,container=snapshot()
        now=time.time();rows=[]
        node_jobs=any(('vitest' in c or ('node' in c and '--import tsx' in c)) and 'maintenance.py' not in c for c in commands)
        temp=Path(subprocess.check_output(['getconf','DARWIN_USER_TEMP_DIR'],text=True).strip())
        for parent in (temp,Path('/private/tmp')):
            progress('temp-discovery',parent)
            for child in parent.iterdir():
                if not child.name.startswith(TEMP_PREFIXES):continue
                if node_jobs and not child.name.startswith('remotion-webpack-bundle-'):continue
                if cache_candidate(child,parent,handles,now-86400):rows.append({'path':str(child),'kind':'temp-cache'})
        # Main checkouts and nested apps can also accumulate abandoned dependencies.
        project_roots=args.projects_root or [Path.home()/'Desktop/projects']
        planned={r['path'] for r in rows}
        discovery=[]
        for projects in project_roots:
            progress('bounded-project-discovery',projects)
            if not projects.exists():
                discovery.append({'root':str(projects),'status':'absent','count':0});continue
            dependencies,scan=bounded_dependency_discovery(projects,state)
            scan['root']=str(projects);discovery.append(scan)
            for child in dependencies:
                if str(child) in planned or not cache_candidate(child,child.parent,handles,now-7*86400):continue
                tree=git_root(child.parent,projects)
                if tree is None or busy_tree(tree,handles,commands,mounts):continue
                relative=str(child.relative_to(tree))
                tracked=subprocess.run(['git','-C',str(tree),'ls-files','--',relative],capture_output=True,text=True,timeout=30)
                ignored=subprocess.run(['git','-C',str(tree),'check-ignore','-q',relative]).returncode==0
                if tracked.returncode==0 and not tracked.stdout and ignored:
                    rows.append({'path':str(child),'tree':str(tree),'kind':'worktree-output'})
                    planned.add(str(child))
        readiness=Path.home()/'.cache/tracelearn-mobile-readiness/staging'
        progress('readiness-discovery',readiness)
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
        report={'at':now,'apply':args.apply,'free_bytes_before':disk_free_bytes(),'dependency_discovery':discovery,'plan':rows,'results':[]}
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
            else:
                try:shutil.rmtree(p);result['result']='removed'
                except FileNotFoundError:result['result']='already_absent'
                except OSError as e:result.update(result='retained_error',error=str(e))
            report['results'].append(result)
            (state/'last-report.json').write_text(json.dumps(report,indent=2)+'\n')
        report['free_bytes_after']=disk_free_bytes()
        report['disk_warning']=report['free_bytes_after']<150*2**30
        report['service_log_budget']=oversized_service_logs(handles)
        if report['service_log_budget']['warnings']:
            with (state/'storage-warnings.jsonl').open('a') as log:
                log.write(json.dumps({'at':time.time(),**report['service_log_budget']})+'\n')
        (state/'last-report.json').write_text(json.dumps(report,indent=2)+'\n')
        progress('complete')
        print(json.dumps({'apply':args.apply,'targets':len(rows),'free_gib':round(report['free_bytes_after']/2**30,1),'warning':report['disk_warning'],'oversized_service_logs':len(report['service_log_budget']['warnings'])}))

if __name__=='__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--scan-dependencies':
        for dependency in project_dependency_roots(Path(sys.argv[2])):
            print(json.dumps({'path':str(dependency)}),flush=True)
    else:main()
