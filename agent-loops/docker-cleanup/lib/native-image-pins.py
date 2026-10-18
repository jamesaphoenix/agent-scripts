#!/usr/bin/env python3
"""Read native image pins without exposing other release proof fields."""
import json
from pathlib import Path
import re
import sys


def main():
    try:
        paths = sys.argv[1].split(':')
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
        print('\n'.join(pins))
        return 0
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        print('Cannot establish all current native image pins; retain images.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
