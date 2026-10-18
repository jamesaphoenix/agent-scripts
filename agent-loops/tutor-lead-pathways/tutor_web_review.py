"""Deliver bounded research reviews to the existing Studio native agent."""
import datetime
import fcntl
import json
import os
from pathlib import Path
import subprocess

from discovery import atomic_json, file_hash, now_iso

RUNTIME = Path('/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts')
CONTROL = RUNTIME / 'state/tutor-web-research-20261018-15h'
AUTH_SHA256 = 'dfc6e06e4c0cd141f2463d8546d49f90130a3ad68c6827f1dc750047ed8df310'
LABEL = 'com.jud.tutor-web-research-20261018-15h-review'
AGENT = 'tutor-bucks-20261017'
HERDR = '/opt/homebrew/bin/herdr'
EXPECTED_AGENT_CWD = str(RUNTIME / '.worktrees/tutor-studio-free-20261017')


def validate(auth):
    expected = {
        'instruction': 'Can you do another 15 hours of web search looking for more tutors in buckinghamshire please?',
        'followUpInstruction': 'I need another 1k tutors plz.',
        'requestedAt': '2026-10-18T18:44:34+00:00',
        'deadline': '2026-10-19T09:44:34+00:00',
        'durationHours': 15,
        'scopeSha256': '626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6',
        'scopeChangesAllowed': False,
        'targetAdditionalDistinctRelevantTutorIdentities': 1000,
        'newMoneyAuthorizedCents': 0,
        'paidDataForSeoSearchesAllowed': False,
        'newPaidClassificationOwnerAllowed': False,
        'maximumSdkTurns': 0,
        'anthropicApiKeyAllowed': False,
        'outreachAllowed': False,
        'newAgentsAllowed': False,
        'nativeWebSearchAllowed': True,
        'monitorIntervalSeconds': 1800,
        'controlRoot': str(RUNTIME / 'state/tutor-web-research-20261018-15h'),
        'existingWorkerDeadline': '2026-10-18T22:06:54+00:00',
        'agent': AGENT,
        'agentSession': 'default',
        'agentPane': 'wA:p1',
        'expectedAgentCwd': EXPECTED_AGENT_CWD,
    }
    if any(type(auth.get(k)) is not type(v) or auth.get(k) != v for k, v in expected.items()):
        raise ValueError('Research authorization changed')
    start = datetime.datetime.fromisoformat(auth['requestedAt'])
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    if deadline - start != datetime.timedelta(hours=15):
        raise ValueError('Research duration changed')
    return start, deadline


def load_authorization():
    path = CONTROL / 'authorization.json'
    if file_hash(path) != AUTH_SHA256:
        raise ValueError('Research authorization hash changed')
    auth = json.loads(path.read_text())
    validate(auth)
    manifest = CONTROL / 'baseline/manifest.json'
    if str(manifest) != auth['baselineManifest'] or file_hash(manifest) != auth['baselineManifestSha256']:
        raise ValueError('Research baseline manifest changed')
    for entry in json.loads(manifest.read_text())['files']:
        if Path(entry['path']).parent != manifest.parent or file_hash(Path(entry['path'])) != entry['sha256']:
            raise ValueError('Research baseline changed')
    return auth


def review_slot(now, start, deadline):
    return min(deadline, max(start, datetime.datetime.fromtimestamp(
        int(now.timestamp()) // 1800 * 1800, datetime.timezone.utc)))


def check(now=None):
    auth = load_authorization()
    start, deadline = validate(auth)
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if now < start:
        return
    if now > deadline + datetime.timedelta(minutes=30):
        # Only stop this reminder. Research and crawler shutdown is agent-owned
        # and separately evidenced, never inferred from this launchd exit.
        atomic_json(CONTROL / 'review-dispatch-final.json', {
            'observedAt': now_iso(), 'deadline': auth['deadline'],
            'actualAgentInspection': False,
            'finalReportPresent': (CONTROL / 'final-report.json').exists(),
            'cleanupReportPresent': (CONTROL / 'cleanup.json').exists()})
        subprocess.run(['/bin/launchctl', 'bootout', f'gui/{os.getuid()}/{LABEL}'],
                       capture_output=True, timeout=10)
        return
    key = review_slot(now, start, deadline).strftime('%Y%m%dT%H%M%SZ')
    delivery = CONTROL / 'deliveries' / (key + '.json')
    with (CONTROL / 'review-dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if delivery.exists() or (CONTROL / 'reviews' / (key + '.json')).exists():
            return
        result = subprocess.run([HERDR, '--session', 'default', 'agent', 'get', AGENT],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            atomic_json(CONTROL / 'dispatcher-error.json', {
                'observedAt': now_iso(), 'slot': key, 'reason': 'Existing native agent unavailable'})
            return
        agent = json.loads(result.stdout)['result']['agent']
        if agent.get('pane_id') != 'wA:p1' or agent.get('cwd') != EXPECTED_AGENT_CWD:
            raise ValueError('Existing Studio native agent target changed')
        final = now >= deadline
        summary = {'observedAt': now_iso(), 'slot': key, 'deadline': auth['deadline'],
                   'agentStatus': agent['agent_status'], 'authorizationSha256': AUTH_SHA256,
                   'actualAgentInspection': False, 'finalReview': final}
        atomic_json(CONTROL / 'inspections' / (key + '.json'), summary)
        prompt = (f'James authorised a new fifteen-hour native web research pass. Read {CONTROL}/HANDOFF.md '
                  f'and {CONTROL}/authorization.json. This replaces the old native-agent research reminder, '
                  'while existing crawler and qualification owners keep their original 2026-10-18T22:06:54Z shutdown. '
                  f'Perform the actual review for slot {key} and write {CONTROL}/reviews/{key}.json '
                  'with reviewedAt, findings, actions, source metrics, duplicates and additional qualified identities. '
                  'Target 1000 additional local academic tutors, prioritising Bucks with Milton Keynes retained. '
                  'Use four measured autoresearch directions and native web search; preserve query and URL caches. '
                  'Zero new paid allowance, no new paid owner, SDK turns, Anthropic key, outreach or agents. '
                  'Keep Fast disabled for this terminal. Do not extend existing owner deadlines. '
                  'Respect the new immutable native research deadline 2026-10-19T09:44:34Z. ')
        prompt += ('The research deadline has arrived: STOP new searches, verify final exports, write final-report.json '
                   'and cleanup.json, and perform task-owned cleanup now.' if final else
                   'Acknowledge the new pass, then continue real research between reviews. Stop old owners at their '
                   'original deadline and keep native free web research going until the new deadline.')
        # Reserve delivery before submission. An interrupted/uncertain submit
        # stays held for inspection rather than replaying a potentially queued prompt.
        atomic_json(delivery, dict(summary, state='submitting', deliveryOnly=True))
        try:
            sent = subprocess.run([HERDR, '--session', 'default', 'agent', 'prompt', AGENT, prompt],
                                  capture_output=True, text=True, timeout=20)
        except (OSError, subprocess.TimeoutExpired):
            atomic_json(delivery, dict(summary, state='uncertain', deliveryOnly=True))
            return
        atomic_json(delivery, dict(summary, state='delivered' if sent.returncode == 0 else 'failed',
                                  exitCode=sent.returncode, deliveryOnly=True))


if __name__ == '__main__':
    check()
