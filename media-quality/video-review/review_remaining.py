#!/usr/bin/env python3
"""Budget-limited audiovisual advisories for the unreviewed Smart Cub exports.

Uses the existing Studio-only Gemini client and authoritative cost ledger.
Never edits human decisions, media, launch approval or automated jitter scans.
"""
import argparse
import hashlib
import importlib.util
import json
import re
from pathlib import Path
import socket
import subprocess

BASE = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
WORK = BASE / 'workflow/cta-11plus-routing-20261021'
OUT = WORK / 'remaining-review-20261026'
AUTH = BASE / 'ledger/media-review-ledger.json'
HELPER = BASE / 'workflow/audio-layout-revision-20261019/marketing-main-source/scripts/review-media.py'
PROMPT = '''Review each attached COMPLETE teaching social video individually, using its
picture and sound. Inspect the entire lesson and its CTA ending, not just the title.
Audience: parents and children aged 9-10, British English. Check teaching accuracy,
examples and answers, readable text, overlaps, captions agreeing with narration,
pronunciation (especially taught vocabulary), natural narration, complete speech,
pacing, relevant CTA and ending. No text-jitter analysis is requested.
New editorial rules from the owner: end with a one-second visual fade AFTER speech;
female/non-founder teaching endings say 'Follow for more', not 'Follow me'; practice
CTAs use the relevant /11-plus/english/, /maths, /non-verbal-reasoning or
/verbal-reasoning/ subject path on ONE readable line. Actual diagnostic/mock offers
must use /free-diagnostic, whereas practice is not a diagnostic. No yellow wipe
transitions. Logos within genuine screenshots are allowed; no duplicate top logo
when the main circle already contains the brand. The attached knownGlobalChanges
are an independent source audit, not evidence you saw a visual defect.
Report observed specific problems and uncertainties without inventing them.
Visual sampling is 0.5 fps: fast/short-lived changes can be missed; audio is supplied
for the complete clip. Do not claim frame-level proof, human approval or publication
readiness. Flag unclear text as uncertain rather than guessing from metadata.
Return ONLY JSON {"reviews":[...]} in input order. Each review must include:
id, aspect, observed_complete (boolean), first_observed (describe the actual opening),
last_observed (describe the actual ending), lesson_observation (the actual taught
rule and at least one worked example you saw), teaching_accuracy (pass/needs_changes/uncertain),
heard_cta (actual words), content_verdict (no_additional_defects_observed / needs_changes /
uncertain), specific_issues (array of {seconds:number,issue,evidence,fix}),
uncertainties (array of strings). You MUST review and describe the teaching body, even when the shared CTA needs
changes. Do not stop at the known CTA problems. Keep each row concise, with precise actionable
findings. Do not repeat knownGlobalChanges in specific_issues unless there is a
separate observed problem. If anything cannot be assessed, state that limitation.'''


def read(p):
    return json.loads(p.read_text())


def sha(p):
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def key(row):
    return '|'.join(row[x] for x in ['kind', 'id', 'aspect', 'sha256'])


def load_media():
    spec = importlib.util.spec_from_file_location('existing_review_media', HELPER)
    media = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(media)
    return media


def summary(media, plan):
    rows = []
    for job_path in sorted(OUT.glob('batch-*.json')):
        if job_path.name.endswith('-response.json'):
            continue
        job = read(job_path)
        if job.get('state') != 'completed_advisory':
            continue
        for item, advisory in zip(job['items'], job['reviews']):
            rows.append({'key': item['key'], 'id': item['id'], 'aspect': item['aspect'],
                         'sourceSha256': item['sha256'], 'knownGlobalChanges': item['knownGlobalChanges'],
                         'advisory': advisory, 'job': str(job_path), 'agentJudgement': 'pending'})
    media.atomic_json(OUT / 'findings.json', {'contract': 'smartcub-remaining-advisory-findings/v1',
        'at': media.ledger.now(), 'planned': plan['total'], 'reviewed': len(rows),
        'remaining': plan['total'] - len(rows), 'publicationApproval': False,
        'humanDecisionsUnchanged': True, 'visualSamplingFps': .5,
        'coverage': 'Complete audio with sampled visual frames; no jitter scan or frame-level proof.',
        'outputs': rows})


def response_schema():
    strings = ['id', 'aspect', 'first_observed', 'last_observed', 'lesson_observation',
               'teaching_accuracy', 'heard_cta', 'content_verdict']
    properties = {k: {'type': 'string'} for k in strings}
    properties.update(observed_complete={'type': 'boolean'},
        specific_issues={'type': 'array', 'items': {'type': 'object', 'properties': {
            'seconds': {'type': 'number'}, **{k: {'type': 'string'} for k in ['issue', 'evidence', 'fix']}},
            'required': ['seconds', 'issue', 'evidence', 'fix']}},
        uncertainties={'type': 'array', 'items': {'type': 'string'}})
    return {'type': 'object', 'properties': {'reviews': {'type': 'array', 'items': {
        'type': 'object', 'properties': properties, 'required': list(properties)}}}, 'required': ['reviews']}


def parse_response(response):
    text = ''.join(p.get('text', '') for p in response['candidates'][0]['content']['parts'])
    text = re.sub(r'^```(?:json)?\s*', '', text.strip())
    text = re.sub(r'\s*```$', '', text)
    answer = json.loads(text)
    for row in answer['reviews']:
        limited = False
        for field in ['first_observed', 'last_observed']:
            if type(row.get(field)) in [int, float]:
                row[field] = 'Model supplied only timestamp ' + str(row[field]) + ', without a content description.'
                limited = True
        if not row.get('lesson_observation'):
            row['lesson_observation'] = 'Not described by the model; teaching-body review needs follow-up.'
            row['teaching_accuracy'] = 'uncertain'
            limited = True
        if limited:
            row['observed_complete'] = False
            row.setdefault('uncertainties', []).append('Insufficient reported evidence of teaching-body coverage; not a completed content check.')
            row['agentDisposition'] = 'needs_teaching_body_followup'
    return answer


def configure(media):
    OUT.mkdir(exist_ok=True)
    plan_path = OUT / 'plan.json'
    if plan_path.exists():
        return read(plan_path)
    manifest = read(WORK / 'rendered-videos/manifest.json')
    human = read(WORK / 'human-review/reviews.json')
    items = []
    subject_paths = {'english': 'english/', 'maths': 'maths',
                     'nvr': 'non-verbal-reasoning', 'vr': 'verbal-reasoning/'}
    for row in manifest['videos']:
        if human['reviews'].get(key(row), {}).get('status') in ['good', 'fix']:
            continue
        if row['kind'] != 'teaching':
            raise ValueError('Unexpected unreviewed scope; inspect before expanding')
        source = WORK / 'rendered-videos' / row['path']
        subject = row['id'].split(':', 1)[1].split('.')[0]
        issues = ['Add one-second visual fade after the complete narration.']
        if row['cta_id'] in ['cta-sc-01', 'cta-sc-02']:
            issues.append('Change female teaching CTA speech and text from Follow me to Follow for more.')
        elif row['cta_id'] == 'cta-sc-10':
            issues.append('Align mock/diagnostic CTA offer, displayed URL and speech with /free-diagnostic.')
        else:
            issues.append('Use subject practice URL smartcublearning.com/11-plus/' + subject_paths[subject] + ' on one readable line.')
            if row['cta_id'] == 'cta-sc-05':
                issues.append('Rewrite this reused CTA as free practice, not a diagnostic, and show 2-6 seconds of genuine product footage.')
            if row['cta_id'] == 'cta-sc-06':
                issues.append('Reduce excessive expressive delivery in this reused CTA.')
            if row['cta_id'] == 'cta-sc-07':
                issues.append('Make the folding/punching card clearly visible and show actual folding.')
        items.append({**{k: row[k] for k in ['id', 'kind', 'aspect', 'sha256', 'title', 'cta_id']},
                      'key': key(row), 'path': str(source), 'knownGlobalChanges': issues})
    plan = {'contract': 'smartcub-remaining-human-unreviewed-plan/v1', 'at': media.ledger.now(),
            'total': len(items), 'items': items, 'model': media.MODEL,
            'visualSamplingFps': 0.5, 'humanReviewSnapshot': human,
            'humanDecisionsNeverOverwritten': True, 'jitterScansEnabled': False,
            'promptSha256': hashlib.sha256(PROMPT.encode()).hexdigest(),
            'budgetLedger': str(AUTH), 'existingTotalCapCents': 10000}
    media.atomic_json(plan_path, plan)
    return plan


def run(media, plan, limit):
    status = {'state': 'starting', 'at': media.ledger.now(), 'total': plan['total'],
              'completedAdvisories': 0, 'skippedHumanReviewed': 0, 'paidRequestsThisRun': 0,
              'estimatedSpentCentsThisRun': 0, 'model': media.MODEL, 'noAutomaticApproval': True}
    def save(state):
        status.update(state=state, at=media.ledger.now())
        media.atomic_json(OUT / 'status.json', status)
        summary(media, plan)
        print(json.dumps(status), flush=True)
    def budget():
        with media.ledger.transaction(AUTH) as live:
            return media.ledger.budget_totals(live['budget'])
    client = media.Gemini.__new__(media.Gemini)
    secret = WORK / '.runtime-secrets/gemini-key'
    if secret.stat().st_mode & 0o077:
        raise ValueError('Runtime credential cache permissions too broad')
    client._key = secret.read_text().strip()
    if client.model()['name'] != 'models/' + media.MODEL:
        raise ValueError('Existing model not available')
    media.tariff_current()
    try:
        for index, start in enumerate(range(0, len(plan['items']), 4), 1):
            items = plan['items'][start:start + 4]
            job_path = OUT / f'batch-{index:03d}.json'
            if job_path.exists():
                job = read(job_path)
                if job['state'] == 'completed_advisory' and sha(Path(job['responsePath'])) == job['responseSha256']:
                    status['completedAdvisories'] += len(items)
                    continue
                if job.get('reservationId'):
                    raise ValueError(f'Unresolved paid request; never automatically retry: {job_path}')
            if limit and status['paidRequestsThisRun'] >= limit:
                save('pilot_complete_remaining_queued')
                return
            if budget()['available_cents'] < 5:
                save('stopped_at_existing_budget_limit')
                return
            files = []
            job = {'state': 'preparing', 'batch': index, 'items': items, 'at': media.ledger.now()}
            media.atomic_json(job_path, job)
            status['currentBatch'] = index
            save('uploading_complete_videos')
            try:
                parts = [{'text': PROMPT}]
                for item in items:
                    path = Path(item['path'])
                    if sha(path) != item['sha256']:
                        raise ValueError('Selected exact output changed: ' + str(path))
                    meta = subprocess.run(['/opt/homebrew/bin/ffprobe', '-v', 'error', '-show_entries',
                        'format=duration', '-of', 'json', str(path)],capture_output=True,text=True,check=True)
                    item['durationSeconds'] = float(json.loads(meta.stdout)['format']['duration'])
                    def remember(file):
                        files.append(file)
                        job['providerFiles'] = files
                        media.atomic_json(job_path, job)
                    file = client.upload(path, remember)
                    parts.extend([{'text': json.dumps({k: item[k] for k in ['id', 'aspect', 'title', 'durationSeconds', 'knownGlobalChanges']})},
                                  {'fileData': {'mimeType': 'video/mp4', 'fileUri': file['uri']},
                                   'videoMetadata': {'fps': 0.5}, 'mediaProcessing': 'STATIC'}])
                body = {'contents': [{'role': 'user', 'parts': parts}], 'generationConfig': {
                    'temperature': 0, 'maxOutputTokens': 4096, 'thinkingConfig': {'thinkingLevel': 'low'},
                    'mediaResolution': 'MEDIA_RESOLUTION_MEDIUM', 'responseMimeType': 'application/json',
                    'responseJsonSchema': response_schema()}}
                counted, _ = client.request('/v1beta/models/' + media.MODEL + ':countTokens',
                    {'generateContentRequest': {'model': 'models/' + media.MODEL, **body}})
                bound = media.preflight(counted.get('totalTokens'))
                job.update(preflight=bound, countTokensReceipt=counted)
                media.atomic_json(job_path, job)
                if bound['maximum_cents'] > budget()['available_cents']:
                    job['state'] = 'budget_preflight_rejected'
                    media.atomic_json(job_path, job)
                    save('stopped_at_existing_budget_limit')
                    return
                identity = hashlib.sha256(json.dumps([i['key'] for i in items]).encode()).hexdigest()
                reservation = 'review:remaining-20261026:' + identity[:32]
                media.ledger.reserve(AUTH, reservation, bound['maximum_cents'], 'review',
                    'Unreviewed teaching exports complete audio and sampled video advisory; ' + str(index))
                job.update(state='submitted_unknown', reservationId=reservation)
                media.atomic_json(job_path, job)
                status['paidRequestsThisRun'] += 1
                save('reviewing_complete_audio_and_sampled_video')
                def headers(identity):
                    job['providerRequestId'] = identity
                    media.atomic_json(job_path, job)
                response, _ = client.request('/v1beta/models/' + media.MODEL + ':generateContent',body,on_headers=headers)
                response_path = OUT / f'batch-{index:03d}-response.json'
                media.atomic_json(response_path, response)
                job.update(state='response_received', responsePath=str(response_path), responseSha256=sha(response_path),
                           usageReceipt=response.get('usageMetadata'), modelVersion=response.get('modelVersion'))
                media.atomic_json(job_path, job)
                if not response.get('modelVersion', '').startswith(media.MODEL):
                    raise ValueError('Unexpected response model')
                cost = media.usage_cost(response.get('usageMetadata', {}))
                if cost['generated_tokens_including_thoughts'] > 4096 or response['usageMetadata']['promptTokenCount'] > bound['maximum_input_tokens']:
                    raise ValueError('Usage exceeded reserved limits; retain hold')
                media.ledger.settle(AUTH, reservation, cost['ledger_cents_rounded_up'],
                    'Gemini remaining teaching audiovisual advisory usage tariff estimate')
                job['costEstimate'] = cost
                status['estimatedSpentCentsThisRun'] += cost['ledger_cents_rounded_up']
                candidate = response['candidates'][0]
                if candidate.get('finishReason') != 'STOP':
                    raise ValueError('Incomplete model response; inspect saved response, no retry')
                answer = parse_response(response)
                rows = answer['reviews']
                if [(r.get('id'), r.get('aspect')) for r in rows] != [(i['id'], i['aspect']) for i in items]:
                    raise ValueError('Missing, reordered or mismatched video review')
                for item, row in zip(items, rows):
                    if type(row.get('observed_complete')) is not bool or row.get('content_verdict') not in ['no_additional_defects_observed', 'needs_changes', 'uncertain']:
                        raise ValueError('Invalid coverage/verdict')
                    if not all(isinstance(row.get(k), str) and row[k].strip() for k in ['first_observed', 'last_observed', 'heard_cta']):
                        raise ValueError('Missing observed coverage')
                    if not isinstance(row.get('specific_issues'), list) or not isinstance(row.get('uncertainties'), list):
                        raise ValueError('Missing issues/uncertainties')
                    for issue in row['specific_issues']:
                        if type(issue.get('seconds')) not in [int, float] or not 0 <= issue['seconds'] <= item['durationSeconds'] + .1:
                            raise ValueError('Invalid issue timestamp')
                        if not all(isinstance(issue.get(k), str) and issue[k].strip() for k in ['issue', 'evidence', 'fix']):
                            raise ValueError('Issue lacks observed evidence')
                job.update(state='completed_advisory', reviews=rows)
                media.atomic_json(job_path, job)
                status['completedAdvisories'] += len(items)
                status['remainingAvailableCents'] = budget()['available_cents']
                save('batch_completed')
            finally:
                cleanup = []
                for file in files:
                    try:
                        client.delete(file['name'])
                        cleanup.append({'name': file['name'], 'deleted': True})
                    except Exception as error:
                        cleanup.append({'name': file['name'], 'deleted': False, 'errorType': type(error).__name__})
                job['providerCleanup'] = cleanup
                media.atomic_json(job_path, job)
        save('all_remaining_advisories_complete_agent_judgement_pending')
    except Exception as error:
        status['error'] = {'type': type(error).__name__, 'message': str(error)[:350]}
        save('stopped_needs_attention_no_automatic_retry')
        raise


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['configure', 'run'])
    parser.add_argument('--max-requests', type=int, default=0)
    args = parser.parse_args()
    if ''.join(c for c in socket.gethostname().split('.')[0].lower() if c.isalnum()) != 'jamessmacstudio':
        raise ValueError('Mac Studio only')
    media = load_media()
    plan = configure(media)
    if args.command == 'configure':
        print(json.dumps({'total': plan['total'], 'plan': str(OUT / 'plan.json')}))
    else:
        with media.exclusive(OUT / '.lock'):
            try:
                run(media, plan, args.max_requests)
            except Exception as error:
                if not (OUT / 'status.json').exists():
                    media.atomic_json(OUT / 'status.json', {
                        'state': 'stopped_needs_attention_no_automatic_retry', 'at': media.ledger.now(),
                        'total': plan['total'], 'completedAdvisories': 0,
                        'error': {'type': type(error).__name__, 'message': str(error)[:350]}})
                raise
