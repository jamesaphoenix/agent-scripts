#!/usr/bin/env bash
set -euo pipefail
[[ "$(id -un)" == jamesphoenix ]] || { echo 'Install only on Mac Studio' >&2; exit 1; }
case "$(hostname -s | tr '[:upper:]' '[:lower:]')" in jamessmacstudio*|jamess-mac-studio*) ;; *) echo 'Studio-only job' >&2; exit 1;; esac
runtime=/Users/jamesphoenix/render-tmp/jud-brand-videos-20261016
[[ -f "$runtime/source/production/brand-videos/delivery_worker.py" ]] || { echo 'Deploy the pinned source snapshot first' >&2; exit 1; }
label=com.jud.brand-video-delivery-20261016
plist="$HOME/Library/LaunchAgents/$label.plist"
mkdir -p "$HOME/Library/LaunchAgents" "$runtime/state"
python3 - "$plist" "$runtime" "$label" <<'PYCODE'
import plistlib,sys
from pathlib import Path
p,runtime,label=sys.argv[1:]
value={'Label':label,'ProgramArguments':['/opt/homebrew/bin/python3',runtime+'/source/production/brand-videos/delivery_worker.py','--state',runtime+'/state','--source',runtime+'/source'],'WorkingDirectory':runtime+'/source','RunAtLoad':True,'KeepAlive':False,'ProcessType':'Background','StandardOutPath':runtime+'/state/delivery.stdout.log','StandardErrorPath':runtime+'/state/delivery.stderr.log','EnvironmentVariables':{'PATH':'/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin'}}
Path(p).write_bytes(plistlib.dumps(value))
PYCODE
launchctl print "gui/$(id -u)/$label" >/dev/null 2>&1 && { echo 'Job already installed; inspect status before reinstallation' >&2; exit 1; }
launchctl bootstrap "gui/$(id -u)" "$plist"
launchctl print "gui/$(id -u)/$label"
