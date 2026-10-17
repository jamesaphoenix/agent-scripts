#!/usr/bin/env python3
"""Argument-only recovery launcher. A concrete Root release is mandatory."""
import importlib.util
import argparse
import json
import os
from pathlib import Path
import sys

HERE = Path(__file__).parent


def command(binding_path):
    spec = importlib.util.spec_from_file_location('pinned_recovery', HERE / 'recovery.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    binding = json.loads(binding_path.read_text())
    if binding.get('contract') != 'root-reviewed-recovery-host-binding/v1':
        raise ValueError('Concrete reviewed recovery host binding required')
    for key in ['implementation', 'config', 'release']:
        module.current(binding[key])
    if module.sha(HERE / 'recovery.py') != binding['implementation']['sha256']:
        raise ValueError('Binding must pin this exact installed implementation')
    config, _ = module.check(Path(binding['config']['path']))
    release = module.read(binding['release']['path'])
    if release.get('contract') != 'root-explicit-review-recovery-release/v1' \
            or release.get('configSha256') != binding['config']['sha256'] \
            or release.get('family') != config['family'] or release.get('allowSingleNewParent') is not True:
        raise ValueError('Exact family/config Root release required')
    if Path(config['status']).exists():
        raise ValueError('Existing recovery status refuses another launch')
    return [str(module.RUN / '.venv/bin/python'), '-B', str(HERE / 'recovery.py'),
            '--config', binding['config']['path'], '--release', binding['release']['path'],
            '--release-sha256', binding['release']['sha256'], '--execute']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--binding', type=Path, default=HERE / 'approved-launch.json')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true')
    mode.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    argv = command(args.binding)
    if args.check:
        print(json.dumps({'state': 'metadata_only_recovery_binding_checked', 'providerCalls': 0}))
    elif args.execute:
        os.execv(argv[0], argv)
