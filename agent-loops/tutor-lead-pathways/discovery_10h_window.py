"""Exact admission for the 18 October Studio continuation."""
import datetime
import json
from pathlib import Path

from discovery import file_hash

INSTRUCTION = 'Can you go again for anohter 8-10 hours.'
AUTH_SHA256 = 'd726841385909b18090103205aa28be69f192165e77e46b2229461844a786905'
PARENT_MANIFEST_SHA256 = '3a5c2271930e84a3e43d2a28feeb6ded0587c8891d0cc60b7b9259d0afe7886e'
RUNTIME = Path('/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts')
CONTROL = RUNTIME / 'state/tutor-studio-20261018-free-10h'
PROFILE = RUNTIME / 'state/bucks-private-tutors-20261018-free-10h'
QUALIFICATION = RUNTIME / 'state/tutor-qualification-20261018-free-10h'
SOURCE = RUNTIME / '.worktrees/tutor-discovery-10h-20261018/agent-loops/tutor-lead-pathways'


def validate(auth):
    start = datetime.datetime.fromisoformat(auth['requestedAt'])
    deadline = datetime.datetime.fromisoformat(auth['deadline'])
    expected = {
        'instruction': INSTRUCTION,
        'followUpInstruction': 'Look for more tutors in bucks etc.',
        'requestedAt': '2026-10-18T12:06:54+00:00',
        'deadline': '2026-10-18T22:06:54+00:00',
        'durationHours': 10,
        'scopeSha256': '626d1ea33c5d87366917a34f22f3fc8deed5fde3b6227503afa2ffc79d10f5c6',
        'scopeChangesAllowed': False,
        'targetDistinctRelevantLocalTutorIdentities': 2000,
        'newMoneyAuthorizedCents': 0,
        'maximumRemainingSpendNano': 3798775900,
        'sharedCombinedCeilingUsd': 15,
        'paidDataForSeoSearchesAllowed': False,
        'decisionsClassificationAllowed': True,
        'maximumSdkTurns': 0,
        'anthropicApiKeyAllowed': False,
        'outreachAllowed': False,
        'monitorIntervalSeconds': 1800,
        'freeContextIntervalSeconds': 300,
        'previousControlRoot': str(RUNTIME / 'state/tutor-studio-20261017-tutor-graph-16h'),
        'previousQualificationRoot': str(RUNTIME / 'state/tutor-qualification-20261017-tutor-graph-16h'),
        'previousProfileRoot': str(RUNTIME / 'state/bucks-private-tutors-20261017-tutor-graph-16h'),
        'controlRoot': str(CONTROL),
        'qualificationRoot': str(QUALIFICATION),
        'profileRoot': str(PROFILE),
        'sourceBranch': 'feat/tutor-discovery-10h-20261018',
        'sourceWorktree': str(SOURCE.parents[1]),
        'graphAdmissionPolicy': 'local-independent-owned-site-v1',
    }
    if (start.tzinfo is None or deadline - start != datetime.timedelta(hours=10)
            or any(type(auth.get(k)) is not type(v) or auth.get(k) != v for k, v in expected.items())):
        raise ValueError('Invalid ten-hour Studio continuation authorization')
    return start, deadline


def verify_files(auth_path):
    if file_hash(auth_path) != AUTH_SHA256:
        raise ValueError('Ten-hour authorization hash changed')
    manifest = Path('/Users/jamesphoenix/agent-runtime/just-understanding-data/agent-scripts/state/tutor-studio-20261017-tutor-graph-16h/final-export-manifest.json')
    if file_hash(manifest) != PARENT_MANIFEST_SHA256:
        raise ValueError('Parent final export manifest changed')
    for entry in json.loads(manifest.read_text())['files']:
        if file_hash(Path(entry['path'])) != entry['sha256']:
            raise ValueError('Parent final export changed: ' + entry['path'])
    return manifest
