#!/usr/bin/env python3
"""Expire numbered overlay and geometry-audit PNGs, never source/native encode frames."""
import argparse,collections,importlib.util,json,os,re,shlex,stat,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent

def root_kind(root):
    parts=root.parts
    if any(re.search(r'(?:^|[-_])unity(?:$|[-_])',p.lower()) for p in parts):return None
    if parts[-2:]==('artifacts','claude-code'):return 'overlay'
    if parts[-2:]==('video-lab','runs'):return 'geometry'
    return None

def directories(root):
    kind=root_kind(root)
    if kind is None or root.is_symlink() or not root.is_dir():return
    for base,dirs,_ in os.walk(root,followlinks=False):
        p=Path(base)
        dirs[:]=[d for d in dirs if not (p/d).is_symlink() and d not in ('node_modules','.git','Library','Temp','assets','public','source','final-frames')]
        if (kind=='overlay' and p.parent.name=='renders' and p.name.startswith('overlay-') and 'episodes' in p.parts) or (kind=='geometry' and p.parent.name=='geometry' and p.name in ('wide','tall')):
            yield p,kind
            dirs[:]=[]

def roots_for_projects(projects):
    """Bounded discovery of the two known rendering projects and their checkouts."""
    roots=set();queue=[(projects,0)];visited=0
    while queue and visited<256:
        p,depth=queue.pop();visited+=1
        if p.is_symlink() or not p.is_dir():continue
        if (p/'.git').exists():
            if p.name not in ('just-understanding-data','trace-learn'):continue
            checkouts=[p]
            raw=subprocess.run(['git','-C',str(p),'worktree','list','--porcelain'],capture_output=True,text=True,timeout=30)
            if raw.returncode==0:
                checkouts += [Path(s[9:]) for s in raw.stdout.splitlines() if s.startswith('worktree ')]
            for tree in checkouts:
                tree=scoped_checkout(tree,projects)
                if tree is None or tree.is_symlink():continue
                for relative in ('artifacts/claude-code','video-lab/runs','apps/prototypes/video-lab/runs'):
                    x=tree/relative
                    if root_kind(x) and x.is_dir() and not x.is_symlink():roots.add(x)
            continue
        if depth<2:
            queue.extend((x,depth+1) for x in p.iterdir() if x.is_dir() and not x.name.startswith('.') and x.name not in ('node_modules','Library','Temp'))
    return sorted(roots)

def scoped_checkout(tree,projects):
    """Normalize case aliases only when the filesystem proves the same scope."""
    if tree.is_relative_to(projects):return tree
    n=len(projects.parts)
    if tuple(s.casefold() for s in tree.parts[:n])!=tuple(s.casefold() for s in projects.parts):return None
    try:
        if not os.path.samefile(Path(*tree.parts[:n]),projects):return None
    except OSError:return None
    return projects.joinpath(*tree.parts[n:])

def identity(s):return s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns

def frame_files(path,kind,cutoff):
    pattern=re.compile(r'^'+('frame' if kind=='overlay' else 'audit')+r'-\d+\.png$')
    result=[]
    for x in path.iterdir():
        if not pattern.fullmatch(x.name):continue
        s=x.lstat()
        if stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and max(s.st_mtime,s.st_ctime)<=cutoff and not getattr(s,'st_flags',0):result.append((x,identity(s),s.st_blocks*512))
    return result

def git_tracked(path):
    for tree in path.parents:
        if (tree/'.git').exists():
            p=subprocess.run(['git','-C',str(tree),'ls-files','--',str(path)],capture_output=True,text=True,timeout=30)
            return p.returncode!=0 or bool(p.stdout.strip())
    return True

def busy(path,root,snapshot):
    handles,commands,mounts,_=snapshot
    key=str(path).casefold().rstrip('/')
    if any(h==key or h.startswith(key+'/') for h in handles):return 'open_handle'
    if any(path==m or path.is_relative_to(m) or m.is_relative_to(path) for m in mounts):return 'container_bind'
    for cmd in commands:
        if 'render_frames.py' in cmd:continue
        try:args=shlex.split(cmd);exe=Path(args[0]).name.lower()
        except (ValueError,IndexError):continue
        render=exe in ('ffmpeg','blender') or (exe in ('node','bun','pnpm','npm','python','python3','python3.14') and re.search(r'render[-_]|remotion.*render|encode[-_]|refine[-_]',cmd,re.I))
        if render and (str(root).casefold() in cmd.casefold() or any(h==str(root).casefold() or h.startswith(str(root).casefold()+'/') for h in handles)):return 'render_process'
    return None

def verified_video(episode):
    for x in (episode/'renders/final.mp4',episode/'native/final.mp4'):
        if x.is_symlink() or not x.is_file():continue
        try:
            p=subprocess.run(['ffprobe','-v','error','-show_format','-show_streams','-of','json',str(x)],capture_output=True,text=True,timeout=30)
            data=json.loads(p.stdout)
            if p.returncode==0 and float(data.get('format',{}).get('duration',0))>0 and any(s.get('codec_type')=='video' and s.get('width',0)>0 for s in data.get('streams',[])):return True
        except (OSError,ValueError,subprocess.TimeoutExpired):continue
    return False

def run(roots,state,apply=False,age_days=7,quiet_seconds=300,snapshot_fn=None,video_fn=verified_video,deadline_seconds=120):
    if age_days<0 or quiet_seconds<0:raise ValueError('Negative retention threshold')
    roots=[Path(p).absolute() for p in roots]
    if any(not root_kind(p) or p.is_symlink() or p.resolve()!=p for p in roots):raise ValueError('Unrecognized or symlinked output root')
    state.mkdir(parents=True,exist_ok=True,mode=0o700);start=time.monotonic();now=time.time();rows=[];videos={};live=None
    if snapshot_fn is None:
        spec=importlib.util.spec_from_file_location('frame_activity',HERE/'maintenance.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);snapshot_fn=m.snapshot
    for root in roots:
        for p,kind in directories(root):
            if time.monotonic()-start>deadline_seconds:
                rows.append({'path':str(root),'result':'retained_deadline'});break
            row={'path':str(p),'kind':kind};s=p.lstat()
            if not stat.S_ISDIR(s.st_mode) or s.st_uid!=os.getuid() or p.resolve()!=p:row['result']='retained_ownership_or_symlink';rows.append(row);continue
            files=frame_files(p,kind,now-age_days*86400)
            if not files:continue
            row.update(files=len(files),allocated_bytes=sum(x[2] for x in files))
            if max(s.st_mtime,s.st_ctime)>time.time()-quiet_seconds:row['result']='retained_recent_writer'
            elif git_tracked(p):row['result']='retained_tracked_source'
            else:
                if kind=='overlay':
                    episode=p.parent.parent
                    if episode not in videos:videos[episode]=video_fn(episode)
                    if not videos[episode]:row['result']='retained_no_verified_final'
                elif not (p/'audit.json').is_file():row['result']='retained_no_audit_metadata'
                if 'result' not in row:
                    if live is None or len(rows)%10==0:live=snapshot_fn()
                    reason=busy(p,root,live)
                    if reason:row['result']='retained_'+reason
                    elif not apply:row['result']='dry_run'
                    else:
                        # Refresh before each directory, then recheck each file identity.
                        reason=busy(p,root,snapshot_fn())
                        if reason:row['result']='retained_'+reason
                        else:
                            removed=[];changed=[];removed_bytes=0
                            for x,old,allocated in files:
                                try:
                                    if identity(x.lstat())!=old:changed.append(x.name);continue
                                    x.unlink();removed.append(x.name);removed_bytes+=allocated
                                except FileNotFoundError:changed.append(x.name)
                            row.update(result=('removed' if not changed else 'removed_partial') if removed else 'retained_changed',removed_files=len(removed),changed_files=len(changed),removed_allocated_bytes=removed_bytes)
                            if removed:(p/'frames-pruned.json').write_text(json.dumps({'at':time.time(),'reason':'Generated PNG retention; original source and final media retained','files':removed},indent=2)+'\n')
            rows.append(row)
            if len(rows)%25==0:print('Frame directories reviewed',len(rows),flush=True)
    summary={'at':time.time(),'apply':apply,'age_days':age_days,'roots':list(map(str,roots)),'results':dict(collections.Counter(r['result'] for r in rows)),'removed_files':sum(r.get('removed_files',0) for r in rows),'removed_directory_allocation_bytes':sum(r.get('removed_allocated_bytes',0) for r in rows),'directories':rows}
    (state/'last-report.json').write_text(json.dumps(summary,indent=2)+'\n');return summary

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--root',type=Path,action='append',required=True);p.add_argument('--state-dir',type=Path,required=True);p.add_argument('--apply',action='store_true');p.add_argument('--age-days',type=float,default=7);p.add_argument('--quiet-seconds',type=int,default=300);a=p.parse_args()
    r=run(a.root,a.state_dir,a.apply,a.age_days,a.quiet_seconds);print(json.dumps({k:v for k,v in r.items() if k!='directories'}))
