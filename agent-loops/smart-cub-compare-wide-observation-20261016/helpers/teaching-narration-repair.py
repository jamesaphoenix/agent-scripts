#!/usr/bin/env python3
"""Explicit bounded section correction. Studio only, one paid POST, no retries.

Original media remain untouched. The plan freezes the original words and chosen
section, approved replacement words, voice/settings and source audio hash.
All dollar costs remain held in the one shared ledger until billing reconciles.
"""
import argparse
import base64
import fcntl
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import urllib.error
import urllib.request

SPEC = importlib.util.spec_from_file_location("repair_ledger", Path(__file__).with_name("review-ledger.py"))
ledger = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ledger)
VOICE = "Xb7hH8MSUJpSbSDYk0k2"
MODEL = "eleven_multilingual_v2"
SETTINGS = {"stability": 0.45, "similarity_boost": 0.8, "speed": 0.87035, "style": 0.25}
RATE = 48000
FRAME_BYTES = 4  # signed 16-bit stereo
LESSONS = {'english.grammar.complete-finite-clause', 'english.grammar.distinguish-contraction-possession',
           'english.grammar.select-pronoun-form', 'english.punctuation.punctuate-list',
           'maths.fdp.convert-decimal-percentage', 'nvr.transform.reflect-figure',
           'english.grammar.distinguish-phrase-clause', 'maths.fdp.recognise-equivalent-fractions'}


def run(*args):
    return subprocess.check_output(args, stderr=subprocess.PIPE)


def atomic(path, value):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w') as handle:
            json.dump(value, handle, indent=2, ensure_ascii=False)
            handle.write('\n');handle.flush();os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def job_directory(plan_path, plan):
    if plan.get('id') not in LESSONS:
        raise ValueError('Only explicitly reviewed grammar correction targets are authorised')
    return plan_path.parent / (plan['id'] + '-' + ledger.digest_json(plan)[:20])


def exact_alignment(response, text):
    for key in ('alignment', 'normalized_alignment'):
        value = response.get(key)
        if isinstance(value, dict) and ''.join(value.get('characters', [])) == text:
            return key, value
    raise ValueError("Saved alignment does not exactly cover the approved spoken text; inspect locally, never resubmit")


def replacement_clock(original, first, last, text, response, section_samples, cut_start, cut_end):
    tokens = text.split()
    if len(tokens) != last - first + 1:
        raise ValueError("Replacement must preserve all word/cue indices")
    _, alignment = exact_alignment(response, text)
    chars, starts, ends = (alignment.get(k, []) for k in ("characters", "character_start_times_seconds", "character_end_times_seconds"))
    if not len(chars) == len(starts) == len(ends):
        raise ValueError("Malformed character alignment")
    duration = section_samples / RATE
    lexical_end = max((i for i, char in enumerate(chars) if char.isalnum()), default=-1)
    if any(not isinstance(s, (float, int)) or not isinstance(e, (float, int)) or not math.isfinite(s)
           or not math.isfinite(e) or s < 0 or e < s
           or s > duration or (e > duration and (i <= lexical_end or e - duration > .0005))
           for i, (s, e) in enumerate(zip(starts, ends))):
        raise ValueError("Alignment exceeds the complete decoded take")
    # Provider timestamps are rounded to milliseconds. Only a trailing silent
    # punctuation/space end may round past decoded duration by half a millisecond.
    # A spoken character or any larger discrepancy still fails closed.
    ends = [min(e, duration) for e in ends]
    if any(starts[i] < starts[i - 1] for i in range(1, len(starts))):
        raise ValueError("Non-monotonic alignment")
    shift_ms = (section_samples - (cut_end - cut_start)) * 1000 / RATE
    words = [dict(w) for w in original]
    spans = list(re.finditer(r'\S+', text))
    for index, span in enumerate(spans, first):
        words[index].update(t=span.group(), s=round(cut_start * 1000 / RATE + starts[span.start()] * 1000),
                            e=round(cut_start * 1000 / RATE + max(ends[span.start():span.end()]) * 1000))
    for index in range(last + 1, len(words)):
        words[index].update(s=round(original[index]['s'] + shift_ms), e=round(original[index]['e'] + shift_ms))
    if first and words[first]['s'] < words[first - 1]['e']:
        raise ValueError("Replacement overlaps previous original speech")
    if last + 1 < len(words) and words[last]['e'] > words[last + 1]['s']:
        raise ValueError("Replacement overlaps following original speech")
    if any(w['i'] != i or w['e'] < w['s'] or w['s'] < 0 for i, w in enumerate(words)) \
            or any(words[i]['s'] < words[i - 1]['e'] for i in range(first + 1, last + 1)):
        raise ValueError("Repaired word clock is invalid")
    return words


def splice_pcm(original, section, cut_start, cut_end):
    if len(original) % FRAME_BYTES or len(section) % FRAME_BYTES:
        raise ValueError("PCM frames must be complete stereo samples")
    if not 0 <= cut_start < cut_end <= len(original) // FRAME_BYTES:
        raise ValueError("Invalid reviewed source section range")
    result = original[:cut_start * FRAME_BYTES] + section + original[cut_end * FRAME_BYTES:]
    prefix, suffix = original[:cut_start * FRAME_BYTES], original[cut_end * FRAME_BYTES:]
    assert result[:len(prefix)] == prefix
    if suffix:
        assert result[-len(suffix):] == suffix
    return result


def preflight(api_get, characters):
    subscription, models, voice = api_get('user/subscription'), api_get('models'), api_get('voices/' + VOICE)
    model = next((v for v in models if v.get('model_id') == MODEL), {})
    sharing = voice.get('sharing') or {}
    if voice.get('voice_id') != VOICE or voice.get('category') != 'premade' \
            or (sharing.get('financial_rewards_enabled') and float(sharing.get('rate', 0)) != 0) \
            or not model.get('can_do_text_to_speech'):
        raise ValueError("Current built-in Alice voice/model pricing category is unverified")
    factors = [v for v in (model.get('token_cost_factor'), (model.get('model_rates') or {}).get('character_cost_multiplier'))
               if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not factors or any(v < 0 or not v < float('inf') for v in factors):
        raise ValueError("Current model quota factors unavailable")
    discount = (model.get('model_rates') or {}).get('cost_discount_multiplier', 1)
    if not isinstance(discount, (int, float)) or discount < 0 or not discount < float('inf'):
        raise ValueError("Invalid current model multiplier")
    maximum = int(characters * max(1, *factors) * max(1, discount) + 32.999)
    used, limit = subscription.get('character_count'), subscription.get('character_limit')
    if not isinstance(used, int) or not isinstance(limit, int) or used < 0 or limit - used < maximum:
        raise ValueError("Insufficient observed included credits; no POST")
    if model.get('maximum_text_length_per_request', characters) < characters:
        raise ValueError("Current provider request limit is too small")
    return {"checkedAt": ledger.now(), "voice": VOICE, "category": voice['category'], "model": MODEL,
            "maximumIncludedCredits": maximum, "observedRemainingCredits": limit - used,
            "usdRateVerified": False, "note": "Included quota verified; retain full USD2 hold, no overage assumed."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--ledger', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    plan = ledger.read(args.plan)
    output = job_directory(args.plan, plan)
    text = plan['replacementSpokenText']
    if not isinstance(text, str) or not text.strip() or len(text) > 2000:
        raise ValueError("Approved replacement exceeds one bounded 2000-character request")
    if plan['voice'] != VOICE or plan['model'] != MODEL or plan['voiceSettings'] != SETTINGS:
        raise ValueError("Repair must retain the source lesson's pinned Alice voice and settings")
    data_path, audio_path = Path(plan['sourceData']), Path(plan['sourceAudio'])
    if ledger.sha256(data_path) != plan['sourceDataSha256'] or ledger.sha256(audio_path) != plan['sourceAudioSha256']:
        raise ValueError("Original source changed")
    data = ledger.read(data_path)
    first, last = plan['firstWord'], plan['lastWord']
    if first < 1 or last >= len(data['words']) - 1 or first > last or len(text.split()) != last - first + 1:
        raise ValueError("Correction must preserve reviewed internal-section word indices")
    expected = [w['t'] for w in data['words'][first:last + 1]]
    changed = [(i + first, a, b) for i, (a, b) in enumerate(zip(expected, text.split())) if a != b]
    if changed != [tuple(x) for x in plan['approvedWordChanges']]:
        raise ValueError("Spoken changes differ from the explicit approved list")
    print(json.dumps({"state": "plan_only" if not args.execute else "executing_on_studio", "id": plan['id'],
                      "characters": len(text), "approvedChanges": changed, "maximumCents": 200,
                      "outputDirectory": str(output)}))
    if not args.execute:
        return
    if socket.gethostname().lower().split('.')[0].replace('-', '') != 'jamessmacstudio':
        raise ValueError("Narration processing and providers run on Mac Studio only")
    if shutil.which('ffmpeg') is None:
        raise ValueError('ffmpeg must be available before any paid provider request')
    output.mkdir(parents=True, exist_ok=True)
    job_path, response_path = output / 'voice-job.json', output / 'provider-response.json'
    with (output / '.voice.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        version = ledger.digest_json(plan)
        if job_path.exists():
            job = ledger.read(job_path)
            if job.get('version') != version or job.get('state') != 'response_saved' \
                    or ledger.sha256(response_path) != job.get('responseSha256') or job.get('providerStatus') != 200:
                raise ValueError("Prior provider request is unresolved. Inspect receipts; no paid retry.")
        else:
            key = run('op', 'read', 'op://api-keys/ElevenLabs/api_key').decode().strip()
            def api_get(endpoint):
                request = urllib.request.Request('https://api.elevenlabs.io/v1/' + endpoint, headers={'xi-api-key': key})
                with urllib.request.urlopen(request, timeout=30) as response:
                    return json.load(response)
            quota = preflight(api_get, len(text))
            body = {"text": text, "model_id": MODEL, "voice_settings": SETTINGS}
            job = {"version": version, "state": "reservation_intent", "requestBody": body, "billingPreflight": quota,
                   "reservationId": 'teaching-voice:' + version[:40], "createdAt": ledger.now()}
            atomic(job_path, job)
            ledger.reserve(args.ledger, job['reservationId'], 200, 'voice',
                           'Reviewed teaching section correction, premade Alice/v2, at most2000chars, included quota verified; hold USD2 until billing reconciled')
            job.update(state='submitted_unknown', submittedAt=ledger.now());atomic(job_path, job)
            request = urllib.request.Request('https://api.elevenlabs.io/v1/text-to-speech/' + VOICE + '/with-timestamps?output_format=mp3_44100_192',
                                             data=json.dumps(body).encode(), headers={'xi-api-key': key, 'Content-Type': 'application/json'}, method='POST')
            try:
                try:
                    response = urllib.request.urlopen(request, timeout=180)
                except urllib.error.HTTPError as error:
                    response = error
                with response:
                    job.update(state='response_headers_received', providerStatus=response.code,
                               providerHeaders={n: response.headers[n] for n in ['request-id', 'history-item-id', 'character-cost'] if response.headers.get(n)})
                    atomic(job_path, job)
                    raw = response.read()
                temporary = response_path.with_suffix('.tmp')
                with temporary.open('wb') as handle:
                    handle.write(raw);handle.flush();os.fsync(handle.fileno())
                os.replace(temporary, response_path)
                job.update(state='response_saved', responseSha256=hashlib.sha256(raw).hexdigest());atomic(job_path, job)
                if job['providerStatus'] != 200:
                    raise ValueError("Provider rejected the request; preserve receipt and hold, never automatically retry")
            finally:
                key = None
        response = ledger.read(response_path)
        section_path = output / 'section.mp3'
        section_path.write_bytes(base64.b64decode(response['audio_base64'], validate=True))
        original_pcm = run('ffmpeg', '-v', 'error', '-i', str(audio_path), '-f', 's16le', '-ac', '2', '-ar', str(RATE), '-')
        section_pcm = run('ffmpeg', '-v', 'error', '-i', str(section_path), '-f', 's16le', '-ac', '2', '-ar', str(RATE), '-')
        # Midpoints of the original measured word gaps preserve both neighbouring
        # original spoken words. Changed joins require a separate listening review.
        start_ms = (data['words'][first - 1]['e'] + data['words'][first]['s']) / 2
        end_ms = (data['words'][last]['e'] + data['words'][last + 1]['s']) / 2
        cut_start, cut_end = round(start_ms * RATE / 1000), round(end_ms * RATE / 1000)
        words = replacement_clock(data['words'], first, last, text, response, len(section_pcm) // FRAME_BYTES, cut_start, cut_end)
        pcm = splice_pcm(original_pcm, section_pcm, cut_start, cut_end)
        raw_path = output / 'repaired.s16le';raw_path.write_bytes(pcm)
        audio_output = output / 'repaired.wav'
        run('ffmpeg', '-v', 'error', '-y', '-f', 's16le', '-ac', '2', '-ar', str(RATE), '-i', str(raw_path), str(audio_output))
        repaired = json.loads(json.dumps(data));repaired.update(words=words, durationMs=round(len(pcm) / FRAME_BYTES * 1000 / RATE))
        for section in repaired['script']['sections']:
            for beat in section['beats']:
                for before, after in plan['scriptReplacements']:
                    beat['narration'] = beat['narration'].replace(before, after)
        atomic(output / 'repaired-data.json', repaired)
        alignment_name, alignment = exact_alignment(response, text)
        proof = {"state": "section_repaired_requires_join_listening_and_final_video_review", "planSha256": ledger.sha256(args.plan),
                 "sourceDataSha256": plan['sourceDataSha256'], "sourceAudioSha256": plan['sourceAudioSha256'],
                 "repairedAudioSha256": ledger.sha256(audio_output), "repairedDataSha256": ledger.sha256(output / 'repaired-data.json'),
                 "sourceCutSamples": [cut_start, cut_end], "newSectionSamples": len(section_pcm) // FRAME_BYTES,
                 "outputSamples": len(pcm) // FRAME_BYTES, "prefixPcmSha256": hashlib.sha256(original_pcm[:cut_start * FRAME_BYTES]).hexdigest(),
                 "suffixPcmSha256": hashlib.sha256(original_pcm[cut_end * FRAME_BYTES:]).hexdigest(),
                 "unchangedOriginalPrefixAndSuffixVerified": True, "approvedWordChanges": changed,
                 "preservedOriginalClockOverlaps": [i for i, w in enumerate(data['words'])
                      if i and w['s'] < data['words'][i - 1]['e'] and not first <= i <= last + 1],
                 "alignmentField": alignment_name,
                 "alignmentTailRounding": [{"characterIndex": i, "character": char,
                       "providerEndSeconds": alignment['character_end_times_seconds'][i],
                       "decodedEndSeconds": len(section_pcm) / FRAME_BYTES / RATE,
                       "reason": "Silent trailing punctuation, within half-millisecond provider timestamp rounding; spoken characters remain inside the decoded take."}
                      for i, char in enumerate(alignment['characters'])
                      if alignment['character_end_times_seconds'][i] > len(section_pcm) / FRAME_BYTES / RATE],
                 "reviewLimit": "Word alignment and gap midpoints are an index, not listening approval; both changed joins still require review."}
        atomic(output / 'repair-proof.json', proof)
        raw_path.unlink()
        print(json.dumps({k: proof[k] for k in ['state', 'outputSamples', 'repairedAudioSha256', 'repairedDataSha256']}))


if __name__ == '__main__':
    main()
