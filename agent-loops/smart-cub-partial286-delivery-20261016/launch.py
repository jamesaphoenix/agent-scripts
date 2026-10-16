#!/usr/bin/env python3
"""Launch exactly the approved partial286 export controller on Studio."""
import hashlib,json,os,re,socket,sys
from pathlib import Path
WRAPPER=Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/workflow/partial286-delivery-execute.py')
PYTHON=Path('/opt/homebrew/opt/python@3.14/bin/python3.14')
EXPECTED='67cdaba1ff57d3323df41db81fde814a1d84f95f7ad9f38e3cee984e25c405c6'
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def command():
 if re.sub('[^a-z0-9]','',socket.gethostname().split('.')[0].lower())!='jamessmacstudio':raise ValueError('Mac Studio required')
 if WRAPPER.is_symlink()or not WRAPPER.is_file()or sha(WRAPPER)!=EXPECTED:raise ValueError('Approved delivery wrapper bytes changed')
 return[str(PYTHON),'-B',str(WRAPPER)]
def main():
 argv=command()
 if sys.argv[1:]==['--check']:
  print(json.dumps({'state':'pinned_delivery_launcher_verified_no_export','wrapperSha256':EXPECTED,'controllerNice':0,'strictHelperNice':10}));return
 if sys.argv[1:]!=['--execute']:raise ValueError('Explicit --execute required')
 if os.getpriority(os.PRIO_PROCESS,0)!=0:raise ValueError('Controller must be normal0; approved wrapper applies helper nice10 once')
 os.execv(argv[0],argv)
if __name__=='__main__':main()
