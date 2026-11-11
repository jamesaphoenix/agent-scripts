#!/usr/bin/env python3
"""Compose isolated ingress/export routes into one host collector. No secret reads."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).parent
IMAGE = 'otel/opentelemetry-collector-contrib:0.163.1@sha256:1175b03003053962012f5a231f5567f66a3e8fdb960c7835c0e48f7aa1555806'


def rewrite_identities(rule, replacements):
    for old, new in replacements.items():
        rule = rule.replace(json.dumps(old), json.dumps(new))
    return rule


PROFILES = ('synology', 'minisforum')
TAILSCALE_IP = '100.124.34.45'      # MinisForum; native ingress is published only here (and health on loopback)
DOCKER_API = '1.44'                 # Docker Engine 29 refuses older client API versions
MINISFORUM_SLICE = 'jud-observability.slice'


def build(profile='synology'):
    """Compose the collector config. `synology` keeps the existing four routes plus the
    private GoWithSolar route; `minisforum` is one collector for the four product routes only."""
    if profile not in PROFILES:
        raise ValueError('unknown profile ' + profile)
    shared = json.loads((ROOT / 'profiles/synology-existing.json').read_text())
    if profile == 'minisforum':
        return minisforum(shared)
    solar = json.loads((ROOT / 'profiles/gowithsolar.json').read_text())
    # Force the project before native Google exporters can inspect resource overrides.
    for name, exporter in shared['exporters'].items():
        if name.startswith('googlecloud/'):
            route = name.split('/',1)[1]
            shared['processors']['resource/'+route]['attributes'].append(
                {'key':'gcp.project.id','value':exporter['project'],'action':'upsert'})
    # Bind solar ingress to its dedicated network-facing port, never resource-attribute routing.
    solar['receivers']['otlp']['protocols']['http']['endpoint'] = '0.0.0.0:4322'
    solar['receivers']['otlp']['protocols'].pop('grpc', None)
    solar['processors']['batch/metrics'] = {'timeout':'1s','send_batch_size':200,'send_batch_max_size':200}
    for name, pipeline in solar['service']['pipelines'].items():
        if name.startswith('metrics'):
            pipeline['processors'] = ['batch/metrics' if p == 'batch' else p for p in pipeline['processors']]
    # These are host constants, not application-supplied routing attributes.
    raw = json.dumps(solar).replace('${env:GCP_PROJECT_ID}', 'gowith-solar-prod').replace('${env:DEPLOYMENT_ENVIRONMENT}', 'production')
    solar = json.loads(raw)
    # Separate authentication and durable storage for the GoWithSolar route.
    solar['extensions']['googleclientauth'] = {}

    solar['extensions']['file_storage']['directory'] = '/var/lib/solar-queue'
    names = {section: {key:'solar/' + key if section == 'processors' else key.split('/')[0]+'/solar'+('/'+key.split('/',1)[1] if '/' in key else '') for key in solar.get(section,{})}
             for section in ('receivers','processors','exporters','extensions')}
    for section in names:
        for key, value in solar[section].items():
            if section == 'extensions' and key == 'health_check':
                continue
            if section == 'processors':
                # Processor IDs must retain their registered type prefix.
                names[section][key] = key.split('/')[0]+'/solar'+('/'+key.split('/',1)[1] if '/' in key else '')
            shared.setdefault(section,{})[names[section][key]] = copy.deepcopy(value)
    # Rewrite finite diagnostic component identities alongside their namespaced IDs.
    # Without this, the existing closed self-metric filter would drop queue/refusal signals.
    guard = shared['processors'][names['processors']['filter/self']]
    replacements = {old:new for section in ('receivers','exporters') for old,new in names[section].items()}
    guard['metric_conditions'] = [
        rewrite_identities(rule, replacements) for rule in guard['metric_conditions']]
    # Use a single host memory limiter; preserve each application's filters/sampling.
    shared['extensions']['file_storage/shared'] = {'directory':'/var/lib/shared-queue','create_directory':True}
    shared['service']['extensions'].append('file_storage/shared')
    for exporter in shared['exporters'].values():
        exporter['sending_queue'] = {'enabled':True,'storage':'file_storage/shared','queue_size':512,'num_consumers':2}
    shared['processors']['memory_limiter']['limit_mib'] = 640
    shared['processors']['memory_limiter']['spike_limit_mib'] = 96
    shared['processors'].pop(names['processors']['memory_limiter'])
    for exporter in solar['exporters']:
        dest = shared['exporters'][names['exporters'][exporter]]
        dest['auth']['authenticator'] = names['extensions']['googleclientauth']
        dest['sending_queue']['storage'] = names['extensions']['file_storage']
    for key in ('googleclientauth','file_storage'):
        shared['service']['extensions'].append(names['extensions'][key])
    for name, pipeline in solar['service']['pipelines'].items():
        shared['service']['pipelines'][name.split('/')[0]+'/solar'+('/self' if name.endswith('/self') else '')] = {
            section:[('memory_limiter' if v == 'memory_limiter' else names[section][v]) for v in values]
            for section,values in pipeline.items()}
    shared['service']['telemetry'] = solar['service']['telemetry']
    return shared

def minisforum(shared):
    """One collector for Octospark and Trace Learn, staging and production, on MinisForum.

    Routes, GCP projects, tail sampling and per-route exporters are exactly the Synology
    profile's; only the host identity, the Docker API version and the container-name
    filters are specific to this host. No GoWithSolar route and no second collector."""
    # Force the project before native Google exporters can inspect resource overrides.
    for name, exporter in shared['exporters'].items():
        if name.startswith('googlecloud/'):
            route = name.split('/', 1)[1]
            shared['processors']['resource/' + route]['attributes'].append(
                {'key': 'gcp.project.id', 'value': exporter['project'], 'action': 'upsert'})
    # Docker 29 has a minimum client API of 1.44.
    shared['receivers']['docker_stats']['api_version'] = DOCKER_API
    # One queue directory, one memory limiter for the whole host process.
    shared['extensions']['file_storage/shared'] = {'directory': '/var/lib/shared-queue', 'create_directory': True}
    shared['service']['extensions'].append('file_storage/shared')
    for exporter in shared['exporters'].values():
        exporter['sending_queue'] = {'enabled': True, 'storage': 'file_storage/shared', 'queue_size': 512, 'num_consumers': 2}
    shared['processors']['memory_limiter']['limit_mib'] = 640
    shared['processors']['memory_limiter']['spike_limit_mib'] = 96
    # Per-project container-name filter on docker_stats: each route keeps only its own compose project's
    # containers (the project's *-data stack matches the same prefix) and drops every other container.
    for route in ROUTES:
        rule = shared['processors']['filter/containers-' + route]['metrics']['datapoint']
        rule[0] = 'attributes["container_name"] != nil and not IsMatch(attributes["container_name"], "^/?%s-.*")' % route
    # The host identity is MinisForum, not the Synology NAS.
    raw = json.dumps(shared).replace('synology-primary', 'minisforum')
    return json.loads(raw)


ROUTES = ('octospark-staging', 'octospark-prod', 'trace-learn-staging', 'trace-learn-prod')
INGRESS = {'octospark-staging': (32018, 4318), 'octospark-prod': (32019, 4319),
           'trace-learn-staging': (32118, 4320), 'trace-learn-prod': (32119, 4321)}


def compose(profile='minisforum'):
    """Compose file for the collector. Synology's is the checked-in compose.synology.json."""
    if profile == 'synology':
        return json.loads((ROOT / 'compose.synology.json').read_text())
    return {
        'services': {'host-collector': {
            'container_name': 'jud-host-otel',
            'image': IMAGE,
            'user': '10001:10001',
            'group_add': ['115'],                 # gid of `docker` on MinisForum: read the docker.sock mount
            'restart': 'always',
            'init': True,
            'mem_limit': '768m',
            'memswap_limit': '768m',
            'cpus': 1,
            'cgroup_parent': MINISFORUM_SLICE,
            'environment': {
                'GOOGLE_APPLICATION_CREDENTIALS': '/var/secrets/google/application-default-credentials.json',
                'GOMEMLIMIT': '576MiB',
                'GOMAXPROCS': '1',
            },
            'command': ['--config=/etc/otel-collector/config.json'],
            'volumes': [
                './config.json:/etc/otel-collector/config.json:ro',
                '/srv/jud/observability/credentials:/var/secrets/google:ro',
                '/var/run/docker.sock:/var/run/docker.sock:ro',
                '/proc:/hostfs/proc:ro',
                '/sys:/hostfs/sys:ro',
                '/srv/jud/observability/queue-shared:/var/lib/shared-queue',
            ],
            'ports': ['%s:%d:%d' % (TAILSCALE_IP, host, container) for host, container in INGRESS.values()]
                     + ['127.0.0.1:32133:13133'],
            'networks': {'telemetry': {'aliases': ['jud-otel']}},
            'logging': {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}},
            'security_opt': ['no-new-privileges:true'],
            'cap_drop': ['ALL'],
            'stop_grace_period': '30s',
        }},
        'networks': {'telemetry': {'external': True, 'name': 'jud-observability'}},
    }


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', choices=PROFILES, default='synology')
    parser.add_argument('--compose', action='store_true', help='print the compose file instead of the collector config')
    args = parser.parse_args()
    print(json.dumps(compose(args.profile) if args.compose else build(args.profile), indent=2))
