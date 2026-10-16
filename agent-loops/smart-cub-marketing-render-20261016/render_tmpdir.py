#!/usr/bin/env python3
"""Verify the canonical Studio render temporary directory without changing leases."""
import json
import os
from pathlib import Path
import subprocess
import tempfile

NODE = '/opt/homebrew/opt/node@22/bin/node'


def verify(canonical, node_directory, python_directory, legacy=Path('/tmp')):
    canonical = Path(canonical).resolve()
    if not canonical.is_dir() or Path(os.environ.get('TMPDIR', '')).resolve() != canonical:
        raise ValueError('TMPDIR must be the canonical Darwin user temporary directory')
    if Path(node_directory).resolve() != canonical or Path(python_directory).resolve() != canonical:
        raise ValueError('Node and Python temporary directories differ from Darwin user directory')
    leases = {str((parent / 'smart-cub-founder-render.lock').resolve())
              for parent in (canonical, Path(legacy))}
    for path in sorted(leases):
        if Path(path).exists():
            raise ValueError('Render lease requires explicit ownership reconciliation: ' + path)
    return {'state': 'canonical_render_tmpdir_verified', 'canonicalTmpdir': str(canonical),
            'nodeTmpdir': str(Path(node_directory).resolve()),
            'pythonTmpdir': str(Path(python_directory).resolve()), 'checkedLeasePaths': sorted(leases),
            'leasesModified': False}


def main():
    canonical = subprocess.check_output(['/usr/bin/getconf', 'DARWIN_USER_TEMP_DIR'], text=True).strip()
    node = subprocess.check_output([NODE, '-p', 'require("node:os").tmpdir()'], text=True).strip()
    print(json.dumps(verify(canonical, node, tempfile.gettempdir())), flush=True)


if __name__ == '__main__':
    main()
