"""Fail-closed policy for one separately authorised complete-clip observation."""
import hashlib
import json
import re
from pathlib import Path

IDENTITY = "teaching:nvr.pattern.compare-attributes"
OUTPUT = "42a8803e77eb8e7fc16cef93cf80b75b018b55dbcaad7d7c6c14e5d788079fb4"
RUN = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016')
ROOT = RUN / 'smart-cub/apps/prototypes/founder-videos'
EVIDENCE = ROOT / 'out/review-complete-compare-wide-followup-v1'
OLD_REQUEST = ROOT / 'out/review-complete/teaching_nvr.pattern.compare-attributes/wide/42a8803e77eb8e7fc16c/request-76f78055ca41267b40200a5c.json'
OLD_SHA = '34269413bf58c0779c6c71c3421ad1735cc04d42ae1dc22f18caa5005ca4cfc7'
LEDGER = RUN / 'ledger/media-review-ledger.json'
APPENDIX = """

COMPLETE-CLIP FOLLOW-UP VERSION compare-wide-checkpoints-v1:
The attached video lasts 02:50.366667 (170.366667 seconds). Review the entire
original audio and sampled picture from 00:00 through its actual end. Explicitly
report content observed near 01:00, 01:40, 02:20 and the complete CTA from
02:39.333 through 02:50.366667 in coverage.limitations. These checkpoints are
coverage requests, not supplied observations. Do not infer unseen pictures or
unheard words from the approved transcript. If you cannot observe an interval,
state its actual bounds, set observed_complete=false, and leave affected checks
uncertain. Do not claim complete observation from the stated duration alone.
This is one distinct follow-up to an earlier completed but incomplete observation.
"""


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require_target(identity, aspect, output_hash=None):
    if identity != IDENTITY or aspect != 'wide' or output_hash not in (None, OUTPUT):
        raise ValueError('Isolated follow-up permits only the exact compare-attributes wide bytes')


def require_bound(bound):
    if not isinstance(bound.get('maximum_cents'), int) or not 0 < bound['maximum_cents'] <= 11:
        raise ValueError('Follow-up reservation exceeds its authorised maximum11c')


def require_args(args):
    if args.ids != [IDENTITY] or args.aspect != 'wide' or args.kinds or args.all:
        raise ValueError('Only one exact wide target is permitted')
    evidence = args.evidence_root if args.evidence_root.is_absolute() else args.root / args.evidence_root
    if args.root.resolve() != ROOT or evidence.resolve() != EVIDENCE:
        raise ValueError('Explicit canonical source and isolated evidence root required')
    if args.ledger is None or args.ledger.expanduser().resolve() != LEDGER or args.max_requests != 1:
        raise ValueError('Sole shared ledger and maximum one request required')
    if args.mode == 'cleanup':
        raise ValueError('Provider cleanup is outside this one-observation scope')


def require_explicit_job(root, selected, expected_sha, actual_path, job, media):
    """No latest-job selection, no original-advisory fallback, no context rewrite."""
    selected = Path(selected)
    selected = selected if selected.is_absolute() else root / selected
    actual = Path(actual_path)
    actual = actual if actual.is_absolute() else root / actual
    if not re.fullmatch('[0-9a-f]{64}', expected_sha or ''):
        raise ValueError('Exact follow-up request SHA required')
    if actual.resolve() != selected.resolve() or not actual.resolve().is_relative_to(EVIDENCE.resolve()):
        raise ValueError('Only the explicitly selected isolated follow-up request is permitted')
    if sha(actual) != expected_sha:
        raise ValueError('Explicit request bytes changed')
    require_target(job.get('id'), job.get('aspect'), job.get('version', {}).get('output_sha256'))
    version = job['version']
    if job.get('state') != 'completed_advisory' or not job.get('budget_state', '').startswith('settled'):
        raise ValueError('Follow-up must be completed with its reservation settled')
    if version.get('implementation_sha256') != sha(Path(media.__file__)):
        raise ValueError('Follow-up implementation identity differs')
    context = json.loads(Path(job['context_path']).read_text())
    technical = json.loads(Path(job['technical_evidence']).read_text())
    prompt = media.make_prompt(context, 'wide', media.technical_summary(technical))
    if version.get('prompt_sha256') != media.text_hash(prompt) or sha(job['prompt_path']) != media.text_hash(prompt):
        raise ValueError('Follow-up prompt identity differs')
    body = json.loads(Path(job['request_body_path']).read_text())
    part = body['contents'][0]['parts'][0]
    if part.get('videoMetadata') != {'fps': 2} or part.get('mediaProcessing') != 'STATIC':
        raise ValueError('Full-video fps2 STATIC scope changed')
    if set(part) != {'fileData', 'videoMetadata', 'mediaProcessing'} or body['contents'][0]['parts'][1]['text'] != prompt:
        raise ValueError('Follow-up body differs from the complete-clip prompt')
    template = media.request_body('FULL_CLIP_URI_REPLACED_AFTER_UPLOAD', prompt, 2)
    if version.get('request_template_sha256') != media.ledger.digest_json(template):
        raise ValueError('Follow-up request template identity differs')
    identity = {k: version[k] for k in ('output_sha256', 'context_sha256', 'prompt_sha256',
                'request_template_sha256', 'implementation_sha256', 'model', 'model_metadata')}
    if job.get('version_key') != media.ledger.digest_json(identity):
        raise ValueError('Follow-up version key differs')
    require_bound(job['preflight'])


def require_terminal_origin():
    if sha(OLD_REQUEST) != OLD_SHA:
        raise ValueError('Original advisory bytes changed')
    old = json.loads(OLD_REQUEST.read_text())
    reservation = json.loads(LEDGER.read_text())['budget']['reservations'][old['reservation_id']]
    require_target(old['id'], old['aspect'], old['version']['output_sha256'])
    if old['state'] != 'completed_advisory' or old['advisory']['observed_complete'] is not False:
        raise ValueError('Original advisory is not known completed and incomplete')
    if reservation['state'] != 'settled' or reservation['actual_cents'] != 3:
        raise ValueError('Original reservation is unresolved or changed')
