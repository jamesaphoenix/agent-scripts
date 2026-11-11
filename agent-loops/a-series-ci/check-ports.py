#!/usr/bin/env python3
"""Validate the host port registry and compose files against it.

    check-ports.py                                  registry self-check (overlaps, bad numbers)
    check-ports.py --compose FILE --project NAME    published ports of FILE must be registered
                                                    for NAME, bound as the range requires
    check-ports.py --who 32080                      which range and project owns a port
    ss -ltnH | check-ports.py --listening           report listeners that are unregistered or bound
                                                    wider than their range allows

Exit status 1 on any overlap or violation. Compose files may be JSON or YAML (PyYAML if
installed, otherwise a small reader for the `ports:` lists is used).
"""
import argparse
from fnmatch import fnmatch
import json
from pathlib import Path
import re
import sys

REGISTRY = Path(__file__).with_name('ports.json')


def load(path=REGISTRY):
    return json.loads(Path(path).read_text())


def expand(range_):
    """Return {port: {'project': pattern-or-name, 'name': ..., 'bind': ...}} for a range."""
    out = {}
    if 'base' in range_:
        for low, high in range_['base']:
            for offset in range_.get('offsets', [0]):
                for port in range(low + offset, high + offset + 1):
                    out[port] = {'projects': range_['projects'], 'name': range_['name'], 'bind': range_['bind']}
    for port, meta in range_.get('ports', {}).items():
        out[int(port)] = {'projects': [meta['project']], 'name': meta.get('name', ''), 'bind': meta.get('bind', range_['bind'])}
    return out


def table(registry):
    """port -> entry, with the range name. Raises ValueError for the first overlap."""
    owners, problems = {}, []
    for range_ in registry['ranges']:
        for port, entry in expand(range_).items():
            if not 1 <= port <= 65535:
                problems.append('%s: port %d out of range' % (range_['name'], port))
            if port in owners:
                problems.append('port %d is in both %s and %s' % (port, owners[port]['range'], range_['name']))
            owners[port] = dict(entry, range=range_['name'])
    return owners, problems


def validate_registry(registry):
    owners, problems = table(registry)
    names = [r['name'] for r in registry['ranges']]
    if len(names) != len(set(names)):
        problems.append('duplicate range names')
    for range_ in registry['ranges']:
        if range_['bind'] not in ('loopback', 'tailscale', 'any'):
            problems.append('%s: invalid bind %r' % (range_['name'], range_['bind']))
    return problems


# ------------------------------------------------------------------ compose parsing

def parse_port(spec):
    """Compose short or long syntax to a list of (host_ip, published_port) pairs.
    Container-only ports (no host publish) return []."""
    if isinstance(spec, dict):
        published = spec.get('published')
        if published in (None, ''):
            return []
        low, _, high = str(published).partition('-')
        return [(spec.get('host_ip', ''), p) for p in range(int(low), int(high or low) + 1)]
    text = re.sub(r'/(tcp|udp|sctp)$', '', str(spec).strip())
    parts = text.split(':')
    if len(parts) == 1:
        return []
    if len(parts) == 2:
        host_ip, published = '', parts[0]
    else:
        host_ip, published = ':'.join(parts[:-2]).strip('[]'), parts[-2]
    low, _, high = published.partition('-')
    if not low.isdigit():
        return []   # ${VAR} interpolation: cannot be checked statically
    return [(host_ip, p) for p in range(int(low), int(high or low) + 1)]


def compose_ports(path):
    """Yield (service, host_ip, port) for each published port in a compose file."""
    text = Path(path).read_text()
    try:
        document = json.loads(text)
    except ValueError:
        try:
            import yaml
            document = yaml.safe_load(text)
        except ImportError:
            document = _mini_yaml(text)
    for service, body in (document.get('services') or {}).items():
        for spec in (body or {}).get('ports') or []:
            for host_ip, port in parse_port(spec):
                yield service, host_ip, port


def _mini_yaml(text):
    services, service, in_ports = {}, None, False
    for line in text.splitlines():
        stripped = line.split('#', 1)[0].rstrip()
        indent = len(stripped) - len(stripped.lstrip())
        if re.fullmatch(r'  ([\w.-]+):', stripped) and indent == 2:
            service = stripped.strip()[:-1]
            services[service] = {'ports': []}
            in_ports = False
        elif service and re.fullmatch(r'\s{4}ports:', stripped):
            in_ports = True
        elif in_ports and stripped.lstrip().startswith('- '):
            services[service]['ports'].append(stripped.lstrip()[2:].strip().strip('"\''))
        elif stripped.strip() and not stripped.lstrip().startswith('- '):
            in_ports = False
    return {'services': services}


# --------------------------------------------------------------------- checks

def bind_ok(host_ip, wanted, tailscale_ip):
    if wanted == 'any':
        return True
    if wanted == 'loopback':
        return host_ip in ('127.0.0.1', '::1', 'localhost')
    return host_ip in (tailscale_ip, '127.0.0.1', '::1')   # tailscale ranges may also bind loopback


def check_compose(registry, path, project):
    owners, _ = table(registry)
    problems = []
    for service, host_ip, port in compose_ports(path):
        where = '%s: service %s publishes %s:%d' % (Path(path).name, service, host_ip or '0.0.0.0', port)
        entry = owners.get(port)
        if not entry:
            problems.append(where + ' which is not in ports.json')
            continue
        if not any(fnmatch(project, pattern) for pattern in entry['projects']):
            problems.append('%s but %s belongs to %s (range %s), not %s' % (where, port, '/'.join(entry['projects']), entry['range'], project))
        if not bind_ok(host_ip, entry['bind'], registry['tailscale_ip']):
            problems.append('%s: range %s requires a %s bind (add the host IP)' % (where, entry['range'], entry['bind']))
    return problems


def check_listening(registry, lines):
    owners, _ = table(registry)
    problems = []
    for line in lines:
        local = line.split()[3] if len(line.split()) >= 4 else line.strip()
        match = re.match(r'^(?:\[([^\]]+)\]|([^:\s%]+)(?:%\S+)?):(\d+)$', local.split('%')[0] if local.startswith('[') else local)
        if not match:
            continue
        host_ip, port = (match.group(1) or match.group(2)), int(match.group(3))
        if host_ip in ('::', '*'):
            host_ip = '0.0.0.0'
        entry = owners.get(port)
        if not entry:
            continue   # unregistered ports (ssh, cups, ephemeral) are out of scope
        if not bind_ok(host_ip, entry['bind'], registry['tailscale_ip']):
            problems.append('port %d (%s) listens on %s but range %s requires %s' % (port, entry['name'], host_ip, entry['range'], entry['bind']))
    return sorted(set(problems))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--registry', default=str(REGISTRY))
    parser.add_argument('--compose')
    parser.add_argument('--project')
    parser.add_argument('--who', type=int)
    parser.add_argument('--listening', action='store_true', help='read `ss -ltnH` from stdin')
    args = parser.parse_args(argv)
    registry = load(args.registry)
    problems = validate_registry(registry)
    if args.who is not None:
        entry = table(registry)[0].get(args.who)
        print(json.dumps(entry) if entry else 'unregistered')
    if args.compose:
        if not args.project:
            parser.error('--compose needs --project')
        problems += check_compose(registry, args.compose, args.project)
    if args.listening:
        problems += check_listening(registry, sys.stdin.read().splitlines())
    for problem in problems:
        print('FAIL ' + problem)
    if not problems and args.who is None:
        owners, _ = table(registry)
        print('ok: %d ports in %d ranges, no overlaps' % (len(owners), len(registry['ranges'])))
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main())
