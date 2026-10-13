#!/usr/bin/env python3
"""Native compute entry point shared by launchd and systemd."""
import json
import os
from pathlib import Path
import sys

role = sys.argv[1]
state = Path(sys.argv[2]).resolve()
config = json.loads((state / 'config.json').read_text())
env = {k: v for k, v in os.environ.items() if not k.startswith('ANTHROPIC')}
env['PATH'] = '/opt/homebrew/opt/node@22/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin'
if role == 'fleet-watch':
    os.execvpe(config['python'], [config['python'], str(Path(__file__).with_name('watch.py'))], env)
elif role == 'qwen':
    env.update(config['qwen'])
    os.execvpe(config['python'], [config['python'], str(Path(__file__).resolve().parents[1] / 'qwen-admission/serve.py')], env)
else:
    # Resolved host envs are private files provisioned for this release, never Git files.
    file = state / 'env' / (role + '.env')
    for line in file.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            if k.startswith('ANTHROPIC') and v:
                raise SystemExit('Anthropic credentials are not permitted')
            env[k] = v
    current = (state / ('current-render' if role == 'render' else 'current-' + role)).resolve()
    os.chdir(current / 'app' if role.startswith('media-') else current)
    if role == 'render':
        binary = str(current / 'render-host')
        os.execvpe(binary, [binary], env)
    elif role in ['media-staging', 'media-prod']:
        os.execvpe(config['node'], [config['node'], 'apps/worker/dist/media-runtime/index.js'], env)
    else:
        raise SystemExit('Unknown compute role')
