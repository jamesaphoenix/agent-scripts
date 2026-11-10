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
env['PATH'] = os.pathsep.join([
    str(Path.home() / '.local/opt/node24/bin'),
    '/opt/node24/bin',
    '/opt/homebrew/bin',
    '/usr/local/bin',
    '/usr/bin',
    '/bin',
    '/usr/sbin',
    '/sbin',
])
if role == 'fleet-watch':
    os.execvpe(config['python'], [config['python'], str(Path(__file__).with_name('watch.py'))], env)
elif role == 'qwen':
    env.update(config['qwen'])
    os.execvpe(config['python'], [config['python'], str(Path(__file__).resolve().parents[1] / 'qwen-admission/serve.py')], env)
else:
    # Resolved host envs are private files provisioned for this release, never Git files.
    file = state / 'env' / (role + '.env')
    if role == 'render-staging' and not (file.is_file() and (state / 'current-render-staging').exists()):
        # Registered before the first split Octospark staging release has provisioned it.
        # Exiting 0 is not restarted by launchd (SuccessfulExit false) or systemd (on-failure).
        print('render-staging is not provisioned yet; exiting without error')
        sys.exit(0)
    for line in file.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            k, v = line.split('=', 1)
            if k.startswith('ANTHROPIC') and v:
                raise SystemExit('Anthropic credentials are not permitted')
            env[k] = v
    current = (state / ('current-' + role)).resolve()
    os.chdir(current / 'app' if role.startswith('media-') else current)
    if role in ['render', 'render-staging']:
        # One render host per environment; its env file names the instance, port and namespace.
        binary = str(current / 'render-host')
        os.execvpe(binary, [binary], env)
    elif role in ['media-staging', 'media-prod']:
        os.execvpe(config['node'], [config['node'], 'apps/worker/dist/media-runtime/index.js'], env)
    else:
        raise SystemExit('Unknown compute role')
