#!/usr/bin/env python3
"""Show what the `minisforum` profile would change against what is deployed today.

Read-only: over SSH it runs `cat <root>/current.json`, tries `cat` of the release's
compose.json, and falls back to `docker inspect jud-host-otel` with a fixed template
(no environment dump: only GOMEMLIMIT, GOMAXPROCS and GOOGLE_APPLICATION_CREDENTIALS are
selected by name on the host). Nothing is written on the host.

    diff-deployed.py user@host            diff against the live host
    diff-deployed.py --deployed FILE      diff against a saved normalised deployed JSON
"""
import argparse
import difflib
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
import build  # noqa: E402

ROOT = '/srv/jud/observability'
KEEP_ENV = ('GOMEMLIMIT', 'GOMAXPROCS', 'GOOGLE_APPLICATION_CREDENTIALS')
INSPECT = (
    "docker inspect jud-host-otel --format '{{json .}}' >/dev/null && "
    "docker inspect jud-host-otel --format '"
    '{"image":{{json .Config.Image}},"user":{{json .Config.User}},"cmd":{{json .Config.Cmd}},'
    '"mem":{{.HostConfig.Memory}},"memswap":{{.HostConfig.MemorySwap}},"nanocpus":{{.HostConfig.NanoCpus}},"cpu_shares":{{.HostConfig.CpuShares}},'
    '"cgroup_parent":{{json .HostConfig.CgroupParent}},"group_add":{{json .HostConfig.GroupAdd}},'
    '"security_opt":{{json .HostConfig.SecurityOpt}},"cap_drop":{{json .HostConfig.CapDrop}},'
    '"restart":{{json .HostConfig.RestartPolicy.Name}},"init":{{json .HostConfig.Init}},'
    '"ports":{{json .HostConfig.PortBindings}},'
    '"log":{{json .HostConfig.LogConfig}},'
    '"mounts":[{{range $i, $m := .Mounts}}{{if $i}},{{end}}{{json (printf "%s:%s:%t" $m.Source $m.Destination $m.RW)}}{{end}}],'
    '"aliases":{{json (index .NetworkSettings.Networks "jud-observability").Aliases}}}\'')
ENV = ("docker inspect jud-host-otel --format '{{range .Config.Env}}{{$p := split . \"=\"}}"
       "{{if or (eq (index $p 0) \"GOMEMLIMIT\") (eq (index $p 0) \"GOMAXPROCS\") (eq (index $p 0) \"GOOGLE_APPLICATION_CREDENTIALS\")}}{{.}}{{println}}{{end}}{{end}}'")


def ssh(host, command):
    done = subprocess.run(['ssh', '-o', 'BatchMode=yes', host, command], capture_output=True, text=True, timeout=60)
    return done.returncode, done.stdout


def normalise_ports(mapping):
    out = []
    for container, binds in (mapping or {}).items():
        for bind in binds:
            out.append('%s:%s->%s' % (bind.get('HostIp') or '0.0.0.0', bind['HostPort'], container.split('/')[0]))
    return sorted(out)


def normalise_compose(compose):
    """Comparable view of a compose.json service (only fields that matter operationally)."""
    service = compose['services']['host-collector']
    ports = []
    for spec in service.get('ports', []):
        ip, host, container = spec.split(':')
        ports.append('%s:%s->%s' % (ip, host, container))
    mem = service.get('mem_limit', '')
    mem_bytes = int(mem[:-1]) * 1024 * 1024 if mem.endswith('m') else None
    volumes = []
    for volume in service.get('volumes', []):
        source, dest, mode = (volume.split(':') + ['rw'])[:3]
        volumes.append('%s:%s:%s' % (source, dest, 'false' if mode == 'ro' else 'true'))
    return {
        'image': service['image'], 'user': service.get('user', ''), 'cmd': service.get('command'),
        'mem_bytes': mem_bytes, 'nanocpus': int(float(service['cpus']) * 1e9) if 'cpus' in service else None,
        'cpu_shares': service.get('cpu_shares'), 'cgroup_parent': service.get('cgroup_parent', ''),
        'group_add': service.get('group_add'), 'security_opt': service.get('security_opt'),
        'cap_drop': service.get('cap_drop'), 'restart': service.get('restart'), 'init': service.get('init'),
        'ports': sorted(ports), 'log': service.get('logging', {}).get('options'),
        'env': {k: v for k, v in service.get('environment', {}).items() if k in KEEP_ENV},
        'mounts': sorted(volumes),
        'aliases': sorted(service.get('networks', {}).get('telemetry', {}).get('aliases', [])),
    }


def deployed_from_host(host):
    code, out = ssh(host, 'cat %s/current.json' % ROOT)
    current = json.loads(out) if not code else {}
    release = current.get('release')
    code, text = ssh(host, 'cat %s/compose.json' % release) if release else (1, '')
    if not code:
        return current, normalise_compose(json.loads(text)), 'release compose.json'
    code, text = ssh(host, INSPECT)
    if code:
        raise SystemExit('cannot read deployed collector (inspect failed)')
    data = json.loads(text)
    code, env_text = ssh(host, ENV)
    env = dict(line.split('=', 1) for line in env_text.splitlines() if '=' in line)
    mounts = []
    for item in data['mounts']:
        source, dest, rw = item.rsplit(':', 2)
        if source.startswith(ROOT + '/releases/'):
            source = './' + source.rsplit('/', 1)[1] if dest.endswith('config.json') else source.replace(release or '', '<release>')
        mounts.append('%s:%s:%s' % (source, dest, rw))
    return current, {
        'image': data['image'], 'user': data['user'], 'cmd': data['cmd'], 'mem_bytes': data['mem'],
        'nanocpus': data['nanocpus'], 'cpu_shares': data['cpu_shares'], 'cgroup_parent': data['cgroup_parent'] or '',
        'group_add': data['group_add'], 'security_opt': data['security_opt'], 'cap_drop': data['cap_drop'],
        'restart': data['restart'], 'init': data['init'], 'ports': normalise_ports(data['ports']),
        'log': data['log'].get('Config'), 'env': env, 'mounts': sorted(mounts), 'aliases': sorted(a for a in (data['aliases'] or []) if a not in ('host-collector', 'jud-host-otel')),  # Docker adds these itself
    }, 'docker inspect of jud-host-otel (the release compose.json is not readable by the SSH user)'


def render(current, deployed, new):
    lines = ['deployed: target=%s release=%s commit=%s' % (current.get('target'), (current.get('release') or '?').rsplit('/', 1)[-1],
                                                           (current.get('source') or {}).get('commit', '?')[:12]),
             'deployed routes (current.json projects): ' + ', '.join('%s -> %s' % kv for kv in sorted((current.get('projects') or {}).items())),
             'new routes: ' + ', '.join(build.ROUTES), '']
    before = json.dumps(deployed, indent=2, sort_keys=True).splitlines()
    after = json.dumps(new, indent=2, sort_keys=True).splitlines()
    lines.extend(difflib.unified_diff(before, after, 'deployed', 'minisforum profile', lineterm='', n=1))
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('host', nargs='?')
    parser.add_argument('--deployed', help='saved JSON: {"current": {...}, "service": {...}}')
    args = parser.parse_args(argv)
    new = normalise_compose(build.compose('minisforum'))
    if args.deployed:
        saved = json.loads(Path(args.deployed).read_text())
        current, deployed, source = saved['current'], saved['service'], 'saved file'
    elif args.host:
        current, deployed, source = deployed_from_host(args.host)
    else:
        parser.error('give user@host or --deployed')
    print('deployed side read from: ' + source)
    print(render(current, deployed, new))
    return 0


if __name__ == '__main__':
    sys.exit(main())
