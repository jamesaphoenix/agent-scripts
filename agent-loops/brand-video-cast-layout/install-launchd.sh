#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo 'Studio owner required' >&2; exit 1; }
case "$(hostname -s | tr '[:upper:]' '[:lower:]')" in jamessmacstudio*|jamess-mac-studio*) ;; *) echo 'Studio-only task' >&2; exit 1;; esac
runtime=/Users/jamesphoenix/render-tmp/jud-overnight-40-20261016
[[ -f "$runtime/source/production/brand-videos/overnight_repair.py" ]] || { echo 'Deploy the frozen snapshot first' >&2; exit 1; }
label=com.jud.brand-video-cast-layout-20261017
plist="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$runtime/state"
python3 - "$plist" "$runtime" "$label" <<'PYCODE'
import plistlib,sys
from pathlib import Path
p,r,l=sys.argv[1:]
value={'Label':l,'ProgramArguments':['/opt/homebrew/bin/python3',r+'/source/production/brand-videos/overnight_repair.py','--state',r+'/state','--source',r+'/source','--mac-state','/Users/jamesaphoenix/Desktop/projects/just-understanding-data/just-understanding-data/artifacts/brand-videos-20261016','--mac-source','/Users/jamesaphoenix/Desktop/projects/just-understanding-data/just-understanding-data/artifacts/brand-videos-20261016/overnight-source','--tutorials','claude-02','claude-03','--aspects','portrait','landscape','--framing','--fix-description','Restore compact portrait flow nodes and animated connectors above captions','--fix-description','Fade the landscape character plate into the teaching panel before its hard edge','--status-prefix','cast-layout'],'WorkingDirectory':r+'/source','RunAtLoad':True,'KeepAlive':False,'ProcessType':'Standard','StandardOutPath':r+'/state/cast-layout.stdout.log','StandardErrorPath':r+'/state/cast-layout.stderr.log','EnvironmentVariables':{'PATH':'/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
Path(p).write_bytes(plistlib.dumps(value))
PYCODE
launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && { echo 'Already installed: reconcile the actual job before restarting' >&2; exit 1; }
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl print "gui/$(id -u)/$label"
