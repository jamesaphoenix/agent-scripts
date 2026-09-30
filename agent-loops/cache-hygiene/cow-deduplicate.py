#!/usr/bin/env python3
"""Share identical tracked worktree assets using APFS clones, preserving editable copies."""
import argparse,ctypes,fcntl,hashlib,importlib.util,json,os,stat,subprocess,time,uuid
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('hygiene',HERE/'maintenance.py')
hygiene=importlib.util.module_from_spec(spec);spec.loader.exec_module(hygiene)
LIB=ctypes.CDLL(None,use_errno=True)
ALLOWED={'.json','.png','.jpg','.jpeg','.webp','.glb','.html','.csv','.ts','.tsx','.js','.md','.txt','.pdf'}
EXCLUDED={'.git','node_modules','.venv','venv','Library','Temp','.data','.artifacts'}

def digest(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for block in iter(lambda:f.read(1048576),b''):h.update(block)
 return h.hexdigest()

def fingerprint(p):
 s=p.lstat();return (s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns,s.st_mode,s.st_nlink,getattr(s,'st_flags',0))

def eligible(p,root,minimum):
 if not p.is_relative_to(root) or p.resolve()!=p or any(part in EXCLUDED for part in p.relative_to(root).parts):return False
 try:s=p.lstat()
 except FileNotFoundError:return False
 return stat.S_ISREG(s.st_mode) and s.st_uid==os.getuid() and s.st_nlink==1 and not getattr(s,'st_flags',0) and s.st_size>=minimum and p.suffix.lower() in ALLOWED

def clone_replace(source,target,expected,root):
 if not eligible(source,root,0) or not eligible(target,root,0):raise ValueError('Unsafe source or target')
 original=fingerprint(target)
 if digest(source)!=expected or digest(target)!=expected:raise ValueError('Content changed before cloning')
 temp=target.parent/('.cow-share-'+uuid.uuid4().hex);created=False
 try:
  if LIB.clonefile(os.fsencode(source),os.fsencode(temp),ctypes.c_uint32(8)):
   code=ctypes.get_errno();raise OSError(code,os.strerror(code))
  created=True
  subprocess.run(['/usr/bin/xattr','-c',str(temp)],check=True,capture_output=True)
  if LIB.copyfile(os.fsencode(target),os.fsencode(temp),None,ctypes.c_uint32(7)):
   code=ctypes.get_errno();raise OSError(code,os.strerror(code))
  if digest(temp)!=expected or digest(target)!=expected or fingerprint(target)!=original:raise ValueError('Content or metadata changed during cloning')
  before=target.stat();after=temp.stat()
  if (before.st_mode,before.st_mtime_ns,before.st_uid,before.st_gid)!=(after.st_mode,after.st_mtime_ns,after.st_uid,after.st_gid):raise ValueError('Clone metadata differs')
  if subprocess.check_output(['/usr/bin/xattr','-lx',str(target)])!=subprocess.check_output(['/usr/bin/xattr','-lx',str(temp)]):raise ValueError('Clone attributes differ')
  os.replace(temp,target)
 finally:
  if created and temp.exists():temp.unlink()

def main():
 ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--worktree-root',type=Path,required=True);ap.add_argument('--state-dir',type=Path,required=True);ap.add_argument('--minimum-kib',type=int,default=256);ap.add_argument('--apply',action='store_true');ap.add_argument('--limit',type=int,default=20000);a=ap.parse_args()
 root=a.worktree_root.absolute()
 if root.resolve()!=root or not root.is_relative_to(Path.home()) or root.name not in ('.worktrees','worktrees'):ap.error('Require an owned real worktrees directory under home')
 if root.stat().st_uid!=os.getuid():ap.error('Worktree root is not owned by this user')
 if a.minimum_kib<=0 or a.limit<=0:ap.error('Minimum size and limit must be positive')
 a.state_dir.mkdir(parents=True,exist_ok=True);lock=(a.state_dir/'lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 statepath=a.state_dir/'shared-files.json';done=json.loads(statepath.read_text()) if statepath.exists() else {}
 handles,commands,mounts,_=hygiene.snapshot();canonical={};rows=[];start=hygiene.disk_free_bytes();count=0
 for tree in sorted(root.iterdir()):
  if tree.is_symlink() or not tree.is_dir() or not (tree/'.git').exists():continue
  if hygiene.busy_tree(tree,handles,commands,mounts):rows.append({'tree':str(tree),'status':'retained-active'});continue
  try:raw=subprocess.check_output(['git','-C',str(tree),'ls-files','-z'],timeout=30,stderr=subprocess.PIPE)
  except (subprocess.CalledProcessError,subprocess.TimeoutExpired):
   rows.append({'tree':str(tree),'status':'retained-invalid-or-busy-git'});continue
  for relative in raw.decode().split('\0'):
   if not relative:continue
   p=tree/relative
   if not eligible(p,root,a.minimum_kib*1024):continue
   key=digest(p);identity=list(fingerprint(p))
   if done.get(str(p))=={'sha256':key,'identity':identity}:
    canonical.setdefault(key,p);continue
   source=canonical.setdefault(key,p)
   if source==p:continue
   row={'path':str(p),'canonical':str(source),'sha256':key,'bytes':p.stat().st_size,'status':'planned'}
   if a.apply:
    try:
     if count%50==0:handles,commands,mounts,_=hygiene.snapshot()
     if hygiene.busy_tree(tree,handles,commands,mounts):row['status']='retained-active'
     else:
      clone_replace(source,p,key,root);row['status']='shared';done[str(p)]={'sha256':key,'identity':list(fingerprint(p))};count+=1
    except (OSError,ValueError,subprocess.SubprocessError) as e:row.update(status='retained-race-or-error',error=str(e))
   rows.append(row)
   if count>=a.limit:break
  statepath.write_text(json.dumps(done)+'\n')
  (a.state_dir/'report.json').write_text(json.dumps({'apply':a.apply,'shared':count,'free_before':start,'free_now':hygiene.disk_free_bytes(),'rows':rows},indent=2)+'\n')
  print(tree.name,'shared',count,flush=True)
  if count>=a.limit:break
 print('Completed',count,'independent APFS clones; net free-space change',hygiene.disk_free_bytes()-start,flush=True)
if __name__=='__main__':main()
