#!/usr/bin/env python3
"""Read native image pins without exposing other release proof fields."""
import json
from pathlib import Path
import re
import sys


DIGEST = r'[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}'


def env_pin(path):
    """Return RENDER_HOST_RENDERER_IMAGE from a host env file, or None when the file is absent.

    During the renderer split the running render role may use an image no release
    receipt names, so its env file pins it too. A present but malformed file fails closed.
    """
    file = Path(path)
    if not file.is_file():
        return None
    for line in file.read_text().splitlines():
        key, sep, value = line.partition('=')
        if sep and key.strip() == 'RENDER_HOST_RENDERER_IMAGE':
            value = value.strip().strip('"\'')
            if not re.fullmatch(DIGEST, value):
                raise ValueError('Invalid native image digest')
            return value
    raise ValueError('Missing RENDER_HOST_RENDERER_IMAGE')


def main():
    try:
        paths = sys.argv[1].split(':')
        env_paths = [p for p in (sys.argv[2].split(':') if len(sys.argv) > 2 else []) if p]
        pins = []
        for path in paths:
            proof = json.loads(Path(path).read_text())
            for key in ('rendererImage', 'mediaImage'):
                image = proof[key]
                if not isinstance(image, str) or not re.fullmatch(
                    r'[a-zA-Z0-9./_-]+@sha256:[a-f0-9]{64}', image
                ):
                    raise ValueError('Invalid native image digest')
                pins.append(image)
        for path in env_paths:
            image = env_pin(path)
            if image:
                pins.append(image)
        print('\n'.join(pins))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        print('Cannot establish all current native image pins; retain images.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
