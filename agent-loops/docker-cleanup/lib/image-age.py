#!/usr/bin/env python3
"""Approve image retirement only with trusted build and local tagging timestamps."""
import argparse
import datetime
import json
import math
import re
import sys


def old_enough(image, hours, now=None):
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError('Image minimum age must be positive')
    now = now or datetime.datetime.now(datetime.timezone.utc)
    times = []
    for raw in (image.get('Created'), image.get('Metadata', {}).get('LastTagTime')):
        if not isinstance(raw, str):
            raise ValueError('Missing image age metadata')
        # Docker emits nine fractional digits; Python 3.7-3.10 accepts at most
        # microsecond precision. Normalize before using the standard parser.
        raw = re.sub(r'(\.\d{6})\d+', r'\1', raw.replace('Z', '+00:00'))
        stamp = datetime.datetime.fromisoformat(raw)
        if stamp.tzinfo is None or stamp.year < 1970:
            raise ValueError('Untrusted image age metadata')
        times.append(stamp)
    return (now - max(times)).total_seconds() >= hours * 3600


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--min-age-hours', type=float, default=24)
    args = parser.parse_args()
    try:
        images = json.load(sys.stdin)
        if not isinstance(images, list) or len(images) != 1:
            raise ValueError('Require one image inspection')
        return 0 if old_enough(images[0], args.min_age_hours) else 1
    except (ValueError, TypeError, AttributeError):
        print('Cannot establish image age; retain the image.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
