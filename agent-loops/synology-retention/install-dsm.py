#!/usr/bin/env python3
"""Register one persistent NAS-local DSM task. Run on Synology with sudo."""
import json
import os
from pathlib import Path
import subprocess

NAME = 'JUD application release retention'
SCRIPT = '/volume1/docker/jud/maintenance/synology-retention/cleanup.sh'
COMMAND = '/bin/sh ' + SCRIPT + ' --apply'


def api(method, **params):
    args = ['/usr/syno/bin/synowebapi', '--exec', 'api=SYNO.Core.TaskScheduler',
            'version=3' if method in ('get', 'create', 'set') else 'version=2', 'method=' + method]
    args.extend(key + '=' + json.dumps(value, separators=(',', ':')) for key, value in params.items())
    result = subprocess.run(args, capture_output=True, text=True, timeout=30, check=True)
    payload = json.loads(result.stdout)
    if not payload.get('success'):
        raise RuntimeError('DSM Task Scheduler ' + method + ' failed')
    return payload.get('data', {})


def install(call=api):
    tasks = call('list').get('tasks', [])
    matches = [t for t in tasks if t['name'] == NAME]
    if len(matches) > 1:
        raise RuntimeError('Duplicate retention tasks; refusing ambiguous update')
    if matches and (matches[0]['type'] != 'script' or matches[0]['owner'] != 'mydocker'):
        raise RuntimeError('Existing task has unexpected type or owner')
    task_id = matches[0]['id'] if matches else -1
    current = call('get', id=task_id, **({'type': 'script'} if task_id == -1 else {}))
    if matches and current['extra'].get('script') != COMMAND:
        raise RuntimeError('Existing task command differs; refusing overwrite')
    schedule = dict(current['schedule'])
    schedule.update(date_type=0, week_day='0,1,2,3,4,5,6', hour=0, minute=23,
                    last_work_hour=23, repeat_hour=1, repeat_min=0)
    params = dict(name=NAME, owner='mydocker', enable=True, schedule=schedule,
                  extra=dict(script=COMMAND, notify_enable=False, notify_if_error=False, notify_mail=''))
    if task_id == -1:
        call('create', type='script', **params)
    else:
        call('set', id=task_id, **params)
    matches = [t for t in call('list')['tasks'] if t['name'] == NAME]
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one registered task')
    result = call('get', id=matches[0]['id'])
    for key in ('name', 'owner', 'enable'):
        if result[key] != params[key]:
            raise RuntimeError('Task verification failed: ' + key)
    if result['type'] != 'script' or result['extra']['script'] != COMMAND:
        raise RuntimeError('Task command verification failed')
    for key in ('date_type', 'week_day', 'hour', 'minute', 'last_work_hour', 'repeat_hour', 'repeat_min'):
        if result['schedule'][key] != schedule[key]:
            raise RuntimeError('Task schedule verification failed: ' + key)
    return result


if __name__ == '__main__':
    if os.geteuid() != 0 or not Path('/usr/syno/bin/synowebapi').is_file():
        raise SystemExit('Run this installer with sudo on Synology DSM')
    if not Path(SCRIPT).is_file() or not Path(SCRIPT).with_name('retention.py').is_file():
        raise SystemExit('Deploy cleanup.sh and retention.py before installing the schedule')
    print(json.dumps(install(), indent=2))
