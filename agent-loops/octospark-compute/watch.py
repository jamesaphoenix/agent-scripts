#!/usr/bin/env python3
"""One bounded fleet observer; remove obsolete Studio services after 48 stable hours."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

STATE = Path.home() / '.local/state/octospark-compute'
STATUS = STATE / 'fleet-health.json'
PROJECTS = ['octospark-staging', 'octospark-prod', 'trace-learn-staging', 'trace-learn-prod']
PUBLIC = ['https://api-staging.octospark.ai/health', 'https://api.octospark.ai/health', 'https://api-staging.tracelearn.app/health', 'https://api.tracelearn.app/health']
RETIRED = {'grafana', 'prometheus', 'node-exporter', 'promtail', 'loki', 'jaeger', 'langfuse-web', 'langfuse-worker', 'langfuse-postgres', 'langfuse-redis', 'langfuse-clickhouse', 'langfuse-minio'}
MIGRATED = {
    'desktop-linux': {'octospark-prod-live', 'octospark-staging-live', 'trace-learn-prod', 'trace-learn-staging'},
    # CI shares this engine. Only the confirmed legacy staging stack is retired.
    'colima-ci': {'octospark-staging-live'},
}
APPLICATIONS = {'api', 'mcp', 'worker', 'redis', 'cloudsql-proxy'}


def studio_containers(context):
    command = ['docker', '--context', context]
    ids = subprocess.check_output(command + ['ps', '-aq'], text=True, timeout=5).split()
    return json.loads(subprocess.check_output(command + ['inspect', *ids], timeout=8)) if ids else []


def assert_primary_retired():
    for context, projects in MIGRATED.items():
        for container in studio_containers(context):
            labels = container['Config'].get('Labels') or {}
            if labels.get('com.docker.compose.project') in projects and labels.get('com.docker.compose.service') in APPLICATIONS and container['State']['Running']:
                raise RuntimeError('Legacy Studio application is still running: ' + context + ':' + container['Name'])


def check():
    assert_primary_retired()
    # No inputs, credentials, workflow starts or agent invocations in health checks.
    remote = '''import json,subprocess,urllib.request
from pathlib import Path
projects = ['octospark-staging','octospark-prod','trace-learn-staging','trace-learn-prod']
proof = [json.loads((Path('/volume1/docker/jud')/p/'current.json').read_text()) for p in projects]
ids = subprocess.check_output(['/usr/local/bin/docker','ps','-q'],text=True,timeout=5).split()
containers = json.loads(subprocess.check_output(['/usr/local/bin/docker','inspect',*ids],timeout=8))
states = []
for c in containers:
 labels = c['Config'].get('Labels') or {}
 project = labels.get('com.docker.compose.project')
 service = labels.get('com.docker.compose.service')
 if (project in projects or project == 'jud-observability') and service in ['api','mcp','worker','redis','cloudsql-proxy','otel-collector']:
  states.append(dict(project=project,service=service,running=c['State']['Running'],health=c['State'].get('Health',{}).get('Status','running'),restarts=c['RestartCount'],image=c['Config']['Image']))
with urllib.request.urlopen('http://127.0.0.1:32133/health/status',timeout=3) as response:
 if response.status != 200: raise RuntimeError('Shared NAS collector not ready')
telemetry = json.loads(Path('/volume1/docker/jud/observability/current.json').read_text())
print(json.dumps(dict(telemetry=telemetry,releases=[dict(project=p['project'],release=p['release'],images=p['images']) for p in proof],containers=states)))'''
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', 'mydocker@100.123.72.113', 'python3 -'], input=remote, capture_output=True, text=True, timeout=30, check=True)
    nas = json.loads(result.stdout)
    if not any(c['project'] == 'jud-observability' and c['service'] == 'otel-collector' and c['running'] for c in nas['containers']):
        raise RuntimeError('Shared NAS collector is not running')
    if set(nas['telemetry']['projects']) != set(PROJECTS):
        raise RuntimeError('Shared NAS collector routing is incomplete')
    for project in PROJECTS:
        for service in ['api', 'worker']:
            if not any(c['project'] == project and c['service'] == service and c['running'] and c['health'] in ['healthy', 'running'] for c in nas['containers']):
                raise RuntimeError('NAS application readiness failed: ' + project + '/' + service)
    for url in PUBLIC:
        request = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 JUD-Fleet-Verification'})
        with urllib.request.urlopen(request, timeout=5) as response:
            if response.status != 200:
                raise RuntimeError('Public API readiness failed')
    native = {}
    for name, port, path in [('render', 8095, '/readyz'), ('media-staging', 8096, '/healthz'), ('media-prod', 8097, '/healthz'), ('qwen', 8080, '/healthz')]:
        host = '100.77.197.1' if name == 'qwen' else '127.0.0.1'
        with urllib.request.urlopen('http://' + host + ':' + str(port) + path, timeout=5) as response:
            native[name] = json.load(response)
    # A blind host is not a stable deployment even though its liveness endpoint responds.
    if native['render'].get('admissionPaused'):
        raise RuntimeError('Render admission paused')
    identity = hashlib.sha256(json.dumps(nas, sort_keys=True).encode()).hexdigest()
    return identity, dict(nas=nas, native=native)


def cleanup_retired():
    removed = []
    for context, projects in MIGRATED.items():
        for container in studio_containers(context):
            labels = container['Config'].get('Labels') or {}
            project = labels.get('com.docker.compose.project')
            service = labels.get('com.docker.compose.service')
            obsolete = context == 'desktop-linux' and project in ['trace-learn', 'tx-agent-kit'] and service in RETIRED
            migrated = project in projects and service in APPLICATIONS | {'otel-collector', 'otel-healthcheck'} and (service in ['otel-collector', 'otel-healthcheck'] or not container['State']['Running'])
            if obsolete or migrated:
                subprocess.run(['docker', '--context', context, 'rm', '-f', container['Id']], check=True, timeout=20, stdout=subprocess.DEVNULL)
                removed.append(context + ':' + container['Name'])
    return removed


os.umask(0o077)
STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
with (STATE / 'fleet-watch.lock').open('w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    previous = json.loads(STATUS.read_text()) if STATUS.exists() else {}
    while True:
        now = time.time()
        try:
            fingerprint, evidence = check()
            continuous = previous.get('healthy') and previous.get('fingerprint') == fingerprint and now - previous.get('checkedAt', 0) < 120
            since = previous['stableSince'] if continuous else now
            removed = previous.get('removed', [])
            if now - since >= 48 * 3600:
                removed = sorted(set(removed + cleanup_retired()))
            current = dict(healthy=True, checkedAt=now, stableSince=since, cleanupEligibleAt=since + 48 * 3600, fingerprint=fingerprint, removed=removed, evidence=evidence)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            current = dict(healthy=False, checkedAt=now, error=type(error).__name__, removed=previous.get('removed', []))
        temporary = STATUS.with_suffix('.tmp')
        temporary.write_text(json.dumps(current, indent=2))
        temporary.replace(STATUS)
        previous = current
        time.sleep(60)
