#!/usr/bin/env python3
"""Expire idle render-tmp job intermediates, Octospark render scratch attempts and Remotion webpack caches.

Render scratch (the render host's RENDER_HOST_TEMP_ROOT, one `render-attempt-*` directory per attempt)
and webpack caches share a 6 hour TTL and a soft cap: above the cap the oldest units go first.
A scratch attempt with an active lease or lock file, an open handle or a container bind is never touched.
"""
import argparse,collections,hashlib,importlib.util,json,os,shutil,stat,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent
HOME=Path.home()
HERDR='/opt/homebrew/bin/herdr'
DAY=86400
JOB_ROOTS=(HOME/'render-tmp',)
CACHE_ROOTS=(HOME/'render-tmp',HOME/'agent-runtime')
# Octospark render host scratch: on the Mac Studio RENDER_HOST_TEMP_ROOT is the 100 GB APFS RenderScratch
# volume; elsewhere hostconfig defaults it to ~/.cache/octospark-render-host. Each attempt is a
# `render-attempt-*` child removed by the host when the render ends; an absent root is reported, never created.
SCRATCH_ROOTS=(Path('/Volumes/RenderScratch'),HOME/'.cache/octospark-render-host')
# Only the host's own attempt directories are candidates, so a volume root's other entries are never touched.
ATTEMPT_PREFIX='render-attempt-'
HOUR=3600
SCRATCH_HOURS=6
CACHE_HOURS=6
# Sized for the Mac Studio's 100 GB APFS scratch quota volume: crossing the 80 GB soft cap evicts the oldest
# units down to the 64 GB low water mark, so the sweep frees a real margin instead of trimming to the cap each hour.
SOFT_CAP_BYTES=80*10**9
LOW_WATER_BYTES=64*10**9
LOW_WATER_FRACTION=LOW_WATER_BYTES/SOFT_CAP_BYTES
# A scratch attempt carrying any of these at its top level belongs to a live render (or an operator) and is skipped.
LEASE_NAMES=frozenset({'KEEP','.lease','.lock','lease','lock','lease.json'})
# Job-level children kept as final outputs, evidence or ledgers. delivery/ anywhere has its own contract.
FINAL_NAMES=frozenset({'out','final','finals','export','exports','output','outputs','evidence','ledger','ledgers','reports','receipts'})
RECEIPT='r2-archive-receipt.json'
DISCOVERY_SKIP=frozenset({'.git','.venv','venv','Library','Temp','__pycache__','.Trash'})

def key(path):return str(path).removeprefix('/private').casefold().rstrip('/')

def inside(path,keys):
    k=key(path);return any(h==k or h.startswith(k+'/') for h in keys)

def herdr_cwds(binary=HERDR,session='default'):
    """Agent working directories. Absent binary or stopped server means no agents; other failures refuse cleanup."""
    try:p=subprocess.run([binary,'--session',session,'agent','list'],capture_output=True,text=True,timeout=30)
    except FileNotFoundError:return 'absent',[]
    try:data=json.loads(p.stdout)
    except ValueError:raise RuntimeError('Unreadable herdr agent list: '+(p.stderr or p.stdout)[-300:])
    if p.returncode:
        if data.get('error',{}).get('code')=='server_not_running':return 'server_not_running',[]
        raise RuntimeError('herdr agent list failed: '+json.dumps(data.get('error'))[:300])
    cwds=[]
    def collect(x):
        if isinstance(x,dict):
            for k,v in x.items():
                if k.endswith('cwd') and isinstance(v,str):cwds.append(key(v))
                else:collect(v)
        elif isinstance(x,list):
            for v in x:collect(v)
    collect(data.get('result',{}));return 'complete',sorted(set(cwds))

def activity(path,snapshot,agents):
    """Reason a path is in use, else None. Agent cwd inside the path or the path inside an agent cwd both count."""
    handles,commands,mounts,_=snapshot;k=key(path)
    if any(a==k or a.startswith(k+'/') for a in agents):return 'herdr_agent'
    if inside(path,handles):return 'open_handle_or_cwd'
    if any(k in c.casefold() for c in commands if 'render_tmp.py' not in c and 'maintenance.py' not in c):return 'process_command'
    if any(path==m or path.is_relative_to(m) or m.is_relative_to(path) for m in mounts):return 'container_bind'
    return None

def scan_job(job,active_after,deadline):
    """One lstat walk: newest write, per-child bytes, delivery/KEEP subtrees, webpack caches and symlinks.
    Stops at the first write newer than active_after, because the job is then active."""
    s=job.lstat();info={'newest':max(s.st_mtime,s.st_ctime),'children':{},'keep':set(),'delivery':[],'caches':[],'links':[],'complete':True,'recent':None}
    if info['newest']>active_after:info['recent']=str(job);return info
    stack=[job]
    while stack:
        if time.monotonic()>deadline:info['complete']=False;info['error']='scan deadline';return info
        d=stack.pop()
        try:entries=list(os.scandir(d))
        except OSError as e:info['complete']=False;info['error']=str(e)[:300];continue
        for e in entries:
            try:s=e.stat(follow_symlinks=False)
            except FileNotFoundError:continue
            p=Path(e.path);w=max(s.st_mtime,s.st_ctime)
            child=info['children'].setdefault(p.relative_to(job).parts[0],{'bytes':0})
            child['bytes']+=s.st_blocks*512
            if w>info['newest']:info['newest']=w
            if w>active_after:info['recent']=str(p);return info
            if stat.S_ISLNK(s.st_mode):
                try:info['links'].append(os.path.normpath(os.path.join(d,os.readlink(p))))
                except OSError:pass
            elif stat.S_ISDIR(s.st_mode):
                if e.name=='delivery' and 'node_modules' not in p.parts and not any(p.is_relative_to(x) for x in info['delivery']):info['delivery'].append(p)
                if e.name=='webpack' and p.parent.name=='.cache' and p.parent.parent.name=='node_modules':info['caches'].append(p)
                stack.append(p)
            elif e.name=='KEEP':info['keep'].add(p.parent)
    return info

def shallow_links(job,depth=4):
    """Symlinks near the top of an active job (copied projects link shared node_modules/public)."""
    links=[];queue=[(job,0)]
    while queue:
        d,n=queue.pop()
        try:entries=list(os.scandir(d))
        except OSError:continue
        for e in entries:
            if e.is_symlink():
                try:links.append(os.path.normpath(os.path.join(d,os.readlink(e.path))))
                except OSError:pass
            elif n<depth and e.is_dir(follow_symlinks=False) and e.name not in ('node_modules','.git','.venv','venv'):queue.append((Path(e.path),n+1))
    return links

def allocated(path):
    s=path.lstat()
    if not stat.S_ISDIR(s.st_mode):return s.st_blocks*512
    total=s.st_blocks*512
    for base,dirs,files in os.walk(path,followlinks=False):
        for name in dirs+files:
            try:total+=os.lstat(os.path.join(base,name)).st_blocks*512
            except OSError:pass
    return total

def intermediate_units(job,info):
    """Maximal subtrees holding no delivery/ or KEEP subtree. Job-level files and final-output names stay."""
    keep=set(info['keep'])|set(info['delivery']);units=[]
    def visit(d,top):
        for e in sorted(os.scandir(d),key=lambda e:e.name):
            p=Path(e.path)
            if p in keep or (top and (e.name in FINAL_NAMES or e.name=='KEEP' or (e.is_file(follow_symlinks=False)))):continue
            if e.is_dir(follow_symlinks=False) and any(k.is_relative_to(p) for k in keep):visit(p,False);continue
            units.append((p,info['children'].get(e.name,{}).get('bytes',0) if top else allocated(p)))
    visit(job,True);return units

def sha256(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(2**20),b''):h.update(block)
    return h.hexdigest()

def delivery_archived(delivery):
    """True only when an R2 archive receipt lists every local file with a matching sha256 and an object key.
    The receipt must come from an archiver that read the objects back from R2; this never infers it."""
    receipt=delivery/RECEIPT
    if receipt.is_symlink() or not receipt.is_file():return False,'retained_delivery_no_r2_receipt'
    try:data=json.loads(receipt.read_text())
    except (OSError,ValueError):return False,'retained_delivery_unreadable_receipt'
    if data.get('store')!='r2' or data.get('status')!='verified' or not data.get('bucket'):return False,'retained_delivery_receipt_not_verified_r2'
    entries={f.get('path'):f for f in data.get('files',[]) if isinstance(f,dict)}
    for base,dirs,files in os.walk(delivery,followlinks=False):
        for name in files:
            p=Path(base)/name;rel=str(p.relative_to(delivery))
            if p==receipt:continue
            if p.is_symlink():return False,'retained_delivery_symlink'
            entry=entries.get(rel)
            if not entry or not entry.get('key') or entry.get('sha256')!=sha256(p):return False,'retained_delivery_hash_unconfirmed'
    return True,'r2_hash_confirmed'

def remove(path):
    s=path.lstat()
    if stat.S_ISDIR(s.st_mode):shutil.rmtree(path)
    else:path.unlink()

def discover_caches(root,deadline):
    """Bounded walk for node_modules/.cache/webpack outside render job roots."""
    found=[];complete=True
    for base,dirs,_ in os.walk(root,followlinks=False):
        if time.monotonic()>deadline:complete=False;break
        for name in list(dirs):
            p=Path(base)/name
            if name=='node_modules':
                dirs.remove(name);w=p/'.cache/webpack'
                if not p.is_symlink() and w.is_dir() and not w.is_symlink() and not (p/'.cache').is_symlink():found.append(w)
            elif name in DISCOVERY_SKIP or p.is_symlink():dirs.remove(name)
    return found,complete

def cache_units(webpack):
    """Each cache name (e.g. default-development) is one unit; last use is the newest pack access or write."""
    units=[]
    for e in os.scandir(webpack):
        p=Path(e.path);s=p.lstat();last=max(s.st_mtime,s.st_atime);size=s.st_blocks*512
        if stat.S_ISDIR(s.st_mode):
            for base,dirs,files in os.walk(p,followlinks=False):
                for name in dirs+files:
                    try:x=os.lstat(os.path.join(base,name))
                    except OSError:continue
                    last=max(last,x.st_mtime,x.st_atime);size+=x.st_blocks*512
        units.append({'path':str(p),'last_use':last,'bytes':size})
    return units

def scan_scratch(attempt,deadline):
    """Newest write and allocated bytes of one scratch attempt; lease markers at the top level mark it live."""
    s=attempt.lstat();newest=max(s.st_mtime,s.st_ctime);size=s.st_blocks*512;lease=None;complete=True
    try:top=list(os.scandir(attempt))
    except OSError as e:return {'newest':newest,'bytes':size,'lease':None,'complete':False,'error':str(e)[:300]}
    for e in top:
        if e.name in LEASE_NAMES:lease=e.name
    stack=[attempt]
    while stack:
        if time.monotonic()>deadline:complete=False;break
        d=stack.pop()
        try:entries=list(os.scandir(d))
        except OSError:complete=False;continue
        for e in entries:
            try:x=e.stat(follow_symlinks=False)
            except FileNotFoundError:continue
            newest=max(newest,x.st_mtime,x.st_ctime);size+=x.st_blocks*512
            if stat.S_ISDIR(x.st_mode):stack.append(Path(e.path))
    return {'newest':newest,'bytes':size,'lease':lease,'complete':complete}

def run(job_roots,cache_roots,state,apply=False,active_hours=48,intermediate_days=7,delivery_days=30,cache_hours=CACHE_HOURS,cache_cap_bytes=20*2**30,cache_quiet_seconds=3600,scan_seconds=900,delete_seconds=900,snapshot_fn=None,herdr_fn=herdr_cwds,now=None,scratch_roots=(),scratch_hours=SCRATCH_HOURS,soft_cap_bytes=SOFT_CAP_BYTES,low_water_bytes=None):
    # Without an explicit low water mark it keeps the default ratio to the cap (80 GB cap -> 64 GB).
    if low_water_bytes is None:low_water_bytes=int(soft_cap_bytes*LOW_WATER_FRACTION)
    if min(active_hours,intermediate_days,delivery_days,cache_hours,cache_cap_bytes,cache_quiet_seconds,scratch_hours,soft_cap_bytes,low_water_bytes)<0:raise ValueError('Negative retention threshold')
    if low_water_bytes>soft_cap_bytes:raise ValueError('Low water mark must not exceed the soft cap')
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    if snapshot_fn is None:
        spec=importlib.util.spec_from_file_location('render_tmp_activity',HERE/'maintenance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);snapshot_fn=m.snapshot
    now=time.time() if now is None else now;start=time.monotonic();uid=os.getuid()
    live=snapshot_fn();herdr_status,agents=herdr_fn()
    jobs=[];rows=[];caches=[]
    job_roots=[Path(r).absolute() for r in job_roots]
    for root in job_roots:
        if root.is_symlink() or not root.is_dir():jobs.append({'job':str(root),'status':'root_absent_or_symlink'});continue
        for job in sorted(root.iterdir()):
            row={'job':str(job)};jobs.append(row);s=job.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid:row.update(status='retained',reason='not_owned_directory');continue
            if (job/'KEEP').exists():row.update(status='active',reason='keep_marker');continue
            reason=activity(job,live,agents)
            if reason:row.update(status='active',reason=reason);continue
            info=scan_job(job,now-active_hours*3600,start+scan_seconds)
            row['newest_write']=info['newest']
            if info['recent']:row.update(status='active',reason='recent_write',recent_path=info['recent']);continue
            if not info['complete']:row.update(status='retained',reason='scan_incomplete',error=info.get('error'));continue
            row.update(status='inactive',info=info)
    # A copied project that symlinks into a sibling (shared node_modules/public) keeps that sibling while it is active.
    active_links=[l for r in jobs if r.get('status')=='active' for l in shallow_links(Path(r['job']))]
    for r in jobs:
        if r.get('status')=='inactive' and any(Path(l).is_relative_to(r['job']) for l in active_links):
            r.update(status='active',reason='symlink_from_active_job');r.pop('info')
    for r in jobs:
        if r.get('status')!='inactive':continue
        job=Path(r['job']);info=r.pop('info');age=now-info['newest']
        r['idle_days']=round(age/DAY,2)
        caches += [(w,job) for w in info['caches']]
        if age>intermediate_days*DAY:
            for p,size in intermediate_units(job,info):rows.append({'path':str(p),'job':str(job),'kind':'render-intermediate','bytes':size,'reason':f'job idle {age/DAY:.1f}d > {intermediate_days}d'})
        for d in info['delivery']:
            row={'path':str(d),'job':str(job),'kind':'render-delivery','bytes':allocated(d)}
            if age<=delivery_days*DAY:row.update(result='retained_delivery_recent')
            else:
                ok,why=delivery_archived(d);row['reason']=why
                if not ok:row['result']=why
            rows.append(row)
    # Webpack caches: per-unit expiry, then a cap across every unit that is safe to touch.
    discovery=[]
    for root in [Path(r).absolute() for r in cache_roots]:
        if any(root==j or root.is_relative_to(j) for j in job_roots):continue
        if root.is_symlink() or not root.is_dir():discovery.append({'root':str(root),'status':'absent'});continue
        found,complete=discover_caches(root,time.monotonic()+60)
        discovery.append({'root':str(root),'status':'complete' if complete else 'timed_out','count':len(found)})
        caches += [(w,None) for w in found]
    # Render scratch attempts: one unit per top-level child of each scratch root. Live attempts (lease or lock
    # file, open handle, container bind, agent cwd) are never candidates; the rest expire after scratch_hours.
    scratch_units=[];scratch_detail=[]
    for root in [Path(r).absolute() for r in scratch_roots]:
        if root.is_symlink() or not root.is_dir():scratch_detail.append({'root':str(root),'status':'absent'});continue
        count=0
        for attempt in sorted(root.iterdir()):
            if not attempt.name.startswith(ATTEMPT_PREFIX):continue
            count+=1;s=attempt.lstat();row={'path':str(attempt),'kind':'render-scratch','project':str(root)}
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=uid:rows.append(dict(row,bytes=0,result='retained_not_owned_directory'));continue
            info=scan_scratch(attempt,start+scan_seconds);row['bytes']=info['bytes'];row['last_use']=info['newest']
            reason=activity(attempt,live,agents)
            if info['lease']:rows.append(dict(row,result='retained_lease_'+info['lease'].strip('.').replace('.json','')));continue
            if reason:rows.append(dict(row,result='retained_'+reason));continue
            if not info['complete']:rows.append(dict(row,result='retained_scan_incomplete',error=info.get('error')));continue
            scratch_units.append(row)
        scratch_detail.append({'root':str(root),'status':'complete','count':count})
    retained_bytes=0;candidates=[]
    expiring=[Path(r['path']) for r in rows if r['kind']=='render-intermediate']
    for webpack,job in caches:
        if any(webpack.is_relative_to(x) for x in expiring):continue
        project=webpack.parent.parent.parent
        reason=None if job else activity(project,live,agents)
        for u in cache_units(webpack):
            u.update(kind='webpack-cache',project=str(project),**({'job':str(job)} if job else {}))
            if reason or now-u['last_use']<cache_quiet_seconds:u['result']='retained_'+(reason or 'recent_use');retained_bytes+=u['bytes'];rows.append(u);continue
            candidates.append(u)
    candidates.sort(key=lambda u:u['last_use']);total=sum(u['bytes'] for u in candidates)
    for u in candidates:
        if now-u['last_use']>cache_hours*HOUR:u['reason']=f'unused {(now-u["last_use"])/HOUR:.1f}h > {cache_hours}h';total-=u['bytes']
        elif total>cache_cap_bytes:u['reason']=f'global cap {cache_cap_bytes/2**30:.0f}GiB, oldest first';total-=u['bytes']
        else:u['result']='retained_within_cap'
        rows.append(u)
    # Scratch TTL, then one soft cap across every remaining scratch attempt and webpack cache unit: above the cap,
    # oldest first down to the low water mark; at or under the cap nothing goes.
    for u in scratch_units:
        if now-u['last_use']>scratch_hours*HOUR:u['reason']=f'idle {(now-u["last_use"])/HOUR:.1f}h > {scratch_hours}h'
        rows.append(u)
    survivors=[u for u in rows if u['kind'] in ('render-scratch','webpack-cache') and 'reason' not in u and u.get('result') in (None,'retained_within_cap')]
    survivors.sort(key=lambda u:u['last_use']);held=sum(u['bytes'] for u in survivors)
    over_cap=held>soft_cap_bytes
    for u in survivors:
        if not over_cap or held<=low_water_bytes:break
        u.pop('result',None);u['reason']=f'soft cap {soft_cap_bytes/10**9:.0f}GB, oldest first to {low_water_bytes/10**9:.0f}GB';held-=u['bytes']
    for u in scratch_units:
        if 'reason' not in u and 'result' not in u:u['result']='retained_within_ttl'
    retained_bytes+=sum(u['bytes'] for u in scratch_units if u.get('result')=='retained_within_ttl')
    # Apply: refresh activity per job/cache project and stop starting new deletions at the deadline.
    deadline=time.monotonic()+delete_seconds;fresh={}
    for row in rows:
        if 'result' in row:continue
        if not apply:row['result']='dry_run';continue
        if time.monotonic()>deadline:row['result']='retained_deadline';continue
        p=Path(row['path']);scope=Path(row.get('job') or row['project'])
        if row['kind']=='render-scratch':
            # A render may have started since the plan: re-check the attempt itself, not its root.
            snap=snapshot_fn();_,agents=herdr_fn();live_reason=activity(p,snap,agents)
            if live_reason:row['result']='retained_'+live_reason;continue
            if any((p/name).exists() for name in LEASE_NAMES):row['result']='retained_lease';continue
        if str(scope) not in fresh:
            snap=snapshot_fn();_,agents=herdr_fn();fresh[str(scope)]=activity(scope,snap,agents) or ('keep_marker' if row.get('job') and (scope/'KEEP').exists() else None)
        if fresh[str(scope)] and row['kind']!='render-scratch':row['result']='retained_'+fresh[str(scope)];continue
        try:
            s=p.lstat()
            if s.st_uid!=uid:row['result']='retained_not_owned';continue
            if row.get('job') and max(s.st_mtime,s.st_ctime)>now-active_hours*3600:row['result']='retained_changed';continue
            if row['kind']=='render-scratch' and scan_scratch(p,time.monotonic()+60)['newest']>row['last_use']+1:row['result']='retained_changed';continue
            remove(p);row['result']='removed'
        except FileNotFoundError:row['result']='already_absent'
        except OSError as e:row.update(result='retained_error',error=str(e)[:300])
    counts=collections.Counter(r['result'] for r in rows)
    summary={'at':time.time(),'apply':apply,'herdr':herdr_status,'job_roots':list(map(str,job_roots)),'cache_discovery':discovery,'scratch_roots':scratch_detail,
        'soft_cap_bytes':soft_cap_bytes,'low_water_bytes':low_water_bytes,'scratch_hours':scratch_hours,'cache_hours':cache_hours,
        'jobs':{'active':sum(r.get('status')=='active' for r in jobs),'inactive':sum(r.get('status')=='inactive' for r in jobs),'retained':sum(r.get('status')=='retained' for r in jobs)},
        'results':dict(counts),'planned_bytes':sum(r['bytes'] for r in rows if r['result'] in ('dry_run','removed')),
        'removed_bytes':sum(r['bytes'] for r in rows if r['result']=='removed'),
        'webpack_cache_bytes_kept':retained_bytes+total,'job_detail':jobs,'rows':rows}
    (state/'last-report.json').write_text(json.dumps(summary,indent=2,default=str)+'\n');return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--job-root',type=Path,action='append',help='Render job root; each child is a job folder. Default ~/render-tmp')
    p.add_argument('--cache-root',type=Path,action='append',help='Webpack cache discovery root. Default ~/render-tmp and ~/agent-runtime')
    p.add_argument('--scratch-root',type=Path,action='append',help='Octospark render host scratch root (RENDER_HOST_TEMP_ROOT); each child is one render attempt. Default /Volumes/RenderScratch and ~/.cache/octospark-render-host')
    p.add_argument('--scratch-hours',type=float,default=SCRATCH_HOURS,help='Idle hours before a render attempt directory is removed (default 6)')
    p.add_argument('--cache-hours',type=float,default=CACHE_HOURS,help='Unused hours before a webpack cache unit is removed (default 6)')
    p.add_argument('--soft-cap-gb',type=float,default=SOFT_CAP_BYTES/10**9,help='Soft cap in GB across render scratch and webpack caches; above it the oldest units go first (default 80)')
    p.add_argument('--low-water-gb',type=float,default=None,help='Above the soft cap the oldest units go until usage is at or under this (default 80%% of the soft cap, 64 for the default cap)')
    p.add_argument('--state-dir',type=Path,required=True);p.add_argument('--apply',action='store_true')
    a=p.parse_args()
    r=run(a.job_root or JOB_ROOTS,a.cache_root or CACHE_ROOTS,a.state_dir,a.apply,cache_hours=a.cache_hours,scratch_roots=a.scratch_root or SCRATCH_ROOTS,scratch_hours=a.scratch_hours,soft_cap_bytes=int(a.soft_cap_gb*10**9),low_water_bytes=None if a.low_water_gb is None else int(a.low_water_gb*10**9))
    print(json.dumps({k:v for k,v in r.items() if k not in ('job_detail','rows')}))
