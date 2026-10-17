#!/usr/bin/env python3
"""Exact-command child pipe for a parent-loaded, memory-only Gemini credential."""
import os
import sys


def secret_for(argv):
    if argv != ['read', 'op://api-keys/Gemini/api_key']:
        raise ValueError('Unsupported credential command')
    value = os.environ.get('SMART_CUB_REVIEW_GEMINI_KEY', '')
    if not value.strip():
        raise ValueError('Parent memory credential unavailable')
    return value


if __name__ == '__main__':
    try:
        os.write(1, secret_for(sys.argv[1:]).encode() + b'\n')
    except ValueError:
        sys.stderr.write('Task memory credential unavailable or command refused\n')
        sys.exit(1)
