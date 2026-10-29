#!/usr/bin/env python3
"""Compose isolated ingress/export routes into one host collector. No secret reads."""
import copy
import json
from pathlib import Path

ROOT = Path(__file__).parent
IMAGE = 'otel/opentelemetry-collector-contrib:0.163.1@sha256:1175b03003053962012f5a231f5567f66a3e8fdb960c7835c0e48f7aa1555806'


def build():
    shared = json.loads((ROOT / 'profiles/synology-existing.json').read_text())
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

if __name__ == '__main__':
    print(json.dumps(build(),indent=2))
