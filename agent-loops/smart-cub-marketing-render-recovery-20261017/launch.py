#!/usr/bin/env python3
"""Reviewed host lifetime: bounded render worker, then one conditional reconciliation."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess

HERE = Path(__file__).parent


def command(path):
    spec = importlib.util.spec_from_file_location('render_recovery', HERE / 'render.py')
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    b = m.read(path)
    if b.get('contract') != 'root-reviewed-marketing-render-recovery-binding/v1':
        raise ValueError('Exact Root-reviewed binding required')
    for key in ['implementation', 'plan', 'release', 'reconciler', 'reconciliationRelease']:
        m.current(b[key])
    if m.sha(HERE / 'render.py') != b['implementation']['sha256'] or m.sha(HERE / 'reconcile.py') != b['reconciler']['sha256']:
        raise ValueError('Exact installed implementation/reconciler required')
    p, original, _ = m.check(Path(b['plan']['path'])); r = m.read(b['release']['path']); reconciliation = m.read(b['reconciliationRelease']['path'])
    if r.get('contract') != 'root-explicit-marketing-render-recovery-release/v1' \
            or r.get('planSha256') != b['plan']['sha256'] or r.get('allowSingleNewParent') is not True:
        raise ValueError('Exact separate Root render release required')
    if reconciliation.get('contract') != 'root-conditional-marketing-producer-reconciliation/v1' \
            or reconciliation.get('planSha256') != b['plan']['sha256'] \
            or reconciliation.get('reconcilerSha256') != b['reconciler']['sha256'] \
            or reconciliation.get('originalFailedStatusSha256') != p['originalFailedStatus']['sha256'] \
            or reconciliation.get('targetState') != 'recovered_complete260_rendered_agent_quality_pending' \
            or reconciliation.get('allowExactlyOneConditionalPublication') is not True:
        raise ValueError('Exact preauthorised conditional terminal release required')
    host_status = Path(p['status']).parent / 'host-dispatch-status.json'
    if Path(p['status']).exists() or host_status.exists():
        raise ValueError('Existing checkpoint refuses relaunch')
    prefix = [str(m.RUN / '.venv/bin/python'), '-B']
    render = prefix + [str(HERE / 'render.py'), '--plan', b['plan']['path'], '--release', b['release']['path'], '--release-sha256', b['release']['sha256'], '--execute']
    reconcile = prefix + [str(HERE / 'reconcile.py'), '--plan', b['plan']['path'], '--release', b['reconciliationRelease']['path'], '--release-sha256', b['reconciliationRelease']['sha256']]
    return m, original, host_status, render, reconcile


def execute(path):
    m, original, status, render, reconcile = command(path)
    state = {'contract': 'root-released-render-recovery-host/v1', 'pid': os.getpid(), 'hostPgid': os.getpgrp(),
             'binding': m.ref(path), 'state': 'launch_intent', 'qualityApproved': False, 'paidRequests': 0}
    original.save(status, state)
    try:
        child = subprocess.Popen(render, cwd=m.APP, start_new_session=True)
        state.update(state='render_worker_active', renderWorkerPid=child.pid, renderWorkerPgid=os.getpgid(child.pid))
        original.save(status, state)
        if child.wait() != 0:
            raise ValueError('Render worker failed; never retry or reconcile')
        state['state'] = 'render_worker_normal_exit_conditional_reconciliation_pending'
        original.save(status, state)
        callback = subprocess.Popen(reconcile, cwd=m.APP, start_new_session=True)
        state.update(state='conditional_reconciliation_active', reconciliationPid=callback.pid, reconciliationPgid=os.getpgid(callback.pid))
        original.save(status, state)
        if callback.wait() != 0:
            raise ValueError('Conditional reconciliation failed; inspect durable intent and current producer status without rollback or retry')
        state['state'] = 'normal_complete260_and_conditional_status_publication_needs_agent_quality_review'
        original.save(status, state)
    except BaseException as error:
        state.update(state='halted_requires_inspection_no_retry', failure={'type': type(error).__name__})
        original.save(status, state)
        raise


if __name__ == '__main__':
    p = argparse.ArgumentParser(); p.add_argument('--binding', type=Path, default=HERE / 'approved-launch.json')
    g = p.add_mutually_exclusive_group(required=True); g.add_argument('--check', action='store_true'); g.add_argument('--execute', action='store_true'); a = p.parse_args()
    if a.check:
        command(a.binding); print(json.dumps({'state': 'metadata_only_checked', 'providerCalls': 0, 'rendered': False}))
    else:
        execute(a.binding)
