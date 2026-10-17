#!/usr/bin/env python3
"""Independently verify teaching derivatives on Studio; never render or approve a video.

Default prints the benchmark plan without opening media. --verify performs metadata,
hash, decoded-frame and decoded-audio checks. --boundary-evidence writes review PNGs
and WAV excerpts on Studio only. CTA quality approval remains the orchestrator's job.
"""
from __future__ import annotations
import argparse
from decimal import Decimal, ROUND_CEILING
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys

APP = Path(__file__).resolve().parents[1]
FPS = 30
RATE = 48000
MIN_FREE_BYTES = 50 * 1024**3
SIZE = {"wide": (1920, 1080), "tall": (1080, 1920)}


def studio():
    if re.sub('[^a-z0-9]', '', socket.gethostname().split('.')[0].lower()) != 'jamessmacstudio':
        raise ValueError('Media verification is restricted to Mac Studio')


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def check_hash(path, expected, label):
    if not re.fullmatch('[a-f0-9]{64}', str(expected)) or digest(path) != expected:
        raise ValueError(f'{label} SHA256 mismatch: {path}')


def streams(meta, aspect):
    videos = [s for s in meta['streams'] if s['codec_type'] == 'video'
              and not s.get('disposition', {}).get('attached_pic')]
    audio = [s for s in meta['streams'] if s['codec_type'] == 'audio']
    if len(videos) != 1 or len(audio) != 1:
        raise ValueError('Expected one picture and one audio stream')
    video, sound = videos[0], audio[0]
    if (video['width'], video['height']) != SIZE[aspect]:
        raise ValueError('Video dimensions do not match selected aspect')
    return video, sound


def segment(meta, aspect):
    video, audio = streams(meta, aspect)
    values = [Decimal(str(s['duration'])) for s in [meta.get('format', {}), video, audio]
              if s.get('duration') not in (None, 'N/A')]
    if not values or any(not d.is_finite() or d <= 0 for d in values):
        raise ValueError('Invalid or missing segment duration')
    # Independent derivation of the existing round-up-to-one-frame concat contract.
    frames = int((max(values) * FPS - Decimal('0.0000001')).to_integral_value(rounding=ROUND_CEILING))
    if abs(float(video.get('start_time', 0)) - float(audio.get('start_time', 0))) > 1 / FPS:
        raise ValueError('Input audio/picture starts differ; timestamp reset requires review')
    return {'frames': frames, 'seconds': frames / FPS,
            'audioSeconds': float(audio['duration']), 'videoSeconds': float(video['duration'])}


def _boxes(handle, start, end):
    position = start
    while position < end:
        handle.seek(position)
        header = handle.read(8)
        if len(header) != 8:
            raise ValueError('Truncated MP4 atom header')
        size, kind = struct.unpack('>I4s', header)
        length = 8
        if size == 1:
            extended = handle.read(8)
            if len(extended) != 8:
                raise ValueError('Truncated extended MP4 atom')
            size, length = struct.unpack('>Q', extended)[0], 16
        if size == 0:
            size = end - position
        if size < length or position + size > end:
            raise ValueError('Invalid MP4 atom bounds')
        yield kind, position + length, position + size
        position += size


def mp4_audio_timing(path):
    """Read the movie clock and single audio edit, without loading media payloads."""
    def payload(handle, box):
        _, start, end = box
        if end - start > 4096:
            raise ValueError('Unsupported oversized MP4 timing atom')
        handle.seek(start)
        value = handle.read(end - start)
        if len(value) != end - start:
            raise ValueError('Truncated MP4 timing atom')
        return value

    def clock(value):
        if not value or value[0] not in [0, 1]:
            raise ValueError('Unsupported MP4 clock version')
        offset = 20 if value[0] else 12
        required = offset + 4 + (8 if value[0] else 4)
        if len(value) < required:
            raise ValueError('Truncated MP4 clock')
        scale = struct.unpack_from('>I', value, offset)[0]
        duration = struct.unpack_from('>Q' if value[0] else '>I', value, offset + 4)[0]
        if not scale or not duration:
            raise ValueError('Invalid MP4 clock')
        return scale, duration

    with Path(path).open('rb') as handle:
        top = list(_boxes(handle, 0, Path(path).stat().st_size))
        movies = [box for box in top if box[0] == b'moov']
        if len(movies) != 1:
            raise ValueError('Expected one MP4 movie atom')
        children = list(_boxes(handle, movies[0][1], movies[0][2]))
        headers = [box for box in children if box[0] == b'mvhd']
        if len(headers) != 1:
            raise ValueError('Expected one MP4 movie clock')
        movie_scale, _ = clock(payload(handle, headers[0]))
        audio = []
        for track in [box for box in children if box[0] == b'trak']:
            nodes = list(_boxes(handle, track[1], track[2]))
            media = [box for box in nodes if box[0] == b'mdia']
            if len(media) != 1:
                raise ValueError('Invalid MP4 track media')
            leaves = list(_boxes(handle, media[0][1], media[0][2]))
            handlers = [box for box in leaves if box[0] == b'hdlr']
            if len(handlers) != 1:
                raise ValueError('Invalid MP4 track handler')
            handler = payload(handle, handlers[0])
            if len(handler) < 12:
                raise ValueError('Truncated MP4 handler')
            if handler[8:12] != b'soun':
                continue
            clocks = [box for box in leaves if box[0] == b'mdhd']
            edits = [box for box in nodes if box[0] == b'edts']
            if len(clocks) != 1 or len(edits) != 1:
                raise ValueError('Expected one audio clock and edit list')
            edit_nodes = list(_boxes(handle, edits[0][1], edits[0][2]))
            lists = [box for box in edit_nodes if box[0] == b'elst']
            if len(lists) != 1:
                raise ValueError('Expected one audio edit list')
            value = payload(handle, lists[0])
            if len(value) < 8 or value[0] not in [0, 1] or struct.unpack_from('>I', value, 4)[0] != 1:
                raise ValueError('Unsupported MP4 audio edit structure')
            fmt = '>Qqhh' if value[0] else '>Iihh'
            if len(value) != 8 + struct.calcsize(fmt):
                raise ValueError('Invalid MP4 audio edit length')
            duration, media_time, rate, fraction = struct.unpack_from(fmt, value, 8)
            scale, encoded = clock(payload(handle, clocks[0]))
            if duration <= 0 or media_time < 0 or (rate, fraction) != (1, 0) or scale != RATE:
                raise ValueError('Unsupported MP4 audio timing')
            audio.append({'movieTimescale': movie_scale, 'editDurationTicks': duration,
                          'mediaTimeSamples': media_time, 'encodedDurationSamples': encoded})
        if len(audio) != 1:
            raise ValueError('Expected one supported MP4 audio track')
        return audio[0]


def check_output(meta, aspect, expected_frames, decoded_audio_samples,
                 complete_audio_end_samples=None, mp4_timing=None):
    video, audio = streams(meta, aspect)
    expected_seconds = expected_frames / FPS
    if Fraction(video['avg_frame_rate']) != FPS:
        raise ValueError('Output is not 30 fps')
    if int(video.get('nb_read_frames', -1)) != expected_frames:
        raise ValueError('Decoded picture frame count differs from complete source plus CTA')
    if abs(float(video['duration']) - expected_seconds) > 0.001:
        raise ValueError('Picture stream duration is short or long')
    if int(audio['sample_rate']) != RATE or int(audio['channels']) != 2:
        raise ValueError('Output must have 48 kHz stereo audio')
    # AAC may decode a final padded packet. It may not remove the expected samples.
    expected_samples = round(expected_seconds * RATE)
    if not expected_samples - 1 <= decoded_audio_samples <= expected_samples + 1024:
        raise ValueError('Decoded audio samples do not cover complete source plus CTA')
    audio_seconds = float(audio['duration'])
    rounding = None
    if complete_audio_end_samples is not None:
        presented_native = Fraction(int(audio['duration_ts'])) * Fraction(audio['time_base']) * RATE
        if complete_audio_end_samples > presented_native:
            raise ValueError('Audio presentation would cut off original source or CTA audio')
    if audio_seconds < expected_seconds - 1 / RATE - 1e-6:
        # FFmpeg MP4's default 1000 Hz movie clock can floor the AAC edit length
        # by <1ms although the encoded track contains every requested sample.
        # Accept only parsed, exact arithmetic proof that the entire discrepancy
        # lies AFTER all original audio, within our deliberately added tail pad.
        if mp4_timing is None or complete_audio_end_samples is None:
            raise ValueError('Audio stream duration is short or unexpectedly long')
        timing = mp4_timing
        if (any(not isinstance(timing.get(k), int) for k in ['movieTimescale', 'editDurationTicks',
                'mediaTimeSamples', 'encodedDurationSamples']) or timing['movieTimescale'] <= 0
                or timing['editDurationTicks'] <= 0 or not 0 <= timing['mediaTimeSamples'] <= 1024):
            raise ValueError('Unsupported MP4 AAC priming or movie timing facts')
        presented = Fraction(timing['editDurationTicks'] * RATE, timing['movieTimescale'])
        native = Fraction(int(audio['duration_ts'])) * Fraction(audio['time_base']) * RATE
        deficit = expected_samples - presented
        if (audio.get('codec_name') != 'aac' or native != presented
                or timing['encodedDurationSamples'] - timing['mediaTimeSamples'] != expected_samples
                or not 0 < deficit < Fraction(RATE, timing['movieTimescale'])
                or not 0 < complete_audio_end_samples <= presented
                or abs(audio_seconds - float(presented / RATE)) > 0.000001):
            raise ValueError('Audio presentation rounding lacks complete original-audio preservation proof')
        rounding = {**timing, 'targetAudioSamples': expected_samples,
                    'presentationSamples': float(presented), 'roundingDeficitSamples': float(deficit),
                    'completeOriginalAudioEndSamples': complete_audio_end_samples,
                    'presentedTailPadSamples': float(presented - complete_audio_end_samples),
                    'interpretation': 'Sub-one-movie-tick edit rounding affects added tail padding only; all original audio remains presented.'}
    elif audio_seconds > expected_seconds + 1024 / RATE + 1e-6:
        raise ValueError('Audio stream duration is short or unexpectedly long')
    if abs(float(video.get('start_time', 0))) > 0.001 or abs(float(audio.get('start_time', 0))) > 0.001:
        raise ValueError('Unexpected output start timestamp')
    return {'expectedFrames': expected_frames, 'decodedFrames': int(video['nb_read_frames']),
            'expectedSeconds': expected_seconds, 'decodedAudioSamples': decoded_audio_samples,
            'expectedAudioSamples': expected_samples, 'aacPaddingSamples': decoded_audio_samples - expected_samples,
            'videoSeconds': float(video['duration']), 'audioSeconds': float(audio['duration']),
            'width': video['width'], 'height': video['height'], 'fps': FPS, 'sampleRate': RATE, 'channels': 2,
            'audioPresentationRounding': rounding}


def check_provenance(source, cta, output, source_def, cta_id, aspect, receipt, cta_receipt):
    if source.stat().st_size != source_def['bytes']:
        raise ValueError('Teaching source byte count mismatch')
    check_hash(source, source_def['sha256'], 'Teaching source')
    check_hash(cta, cta_receipt['output']['sha256'], 'Rendered CTA')
    check_hash(output, receipt['output']['sha256'], 'Derivative')
    dep = receipt['dependencies']
    dep_hash = hashlib.sha256(json.dumps(dep, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    if receipt['dependencyHash'] != dep_hash:
        raise ValueError('Derivative dependency receipt fingerprint mismatch')
    if (dep['source']['sha256'] != source_def['sha256'] or dep['ctaSha256'] != digest(cta)
            or dep['ctaId'] != cta_id or dep['aspect'] != aspect):
        raise ValueError('Derivative source/CTA dependencies differ from current selected inputs')
    return {'sourceSha256': digest(source), 'ctaSha256': digest(cta), 'outputSha256': digest(output)}


def boundary_plan(source_segment, cta_segment, source_video_frames, cta_video_frames):
    splice = source_segment['frames']
    end = splice + cta_segment['frames']
    if min(source_video_frames, cta_video_frames) < 1:
        raise ValueError('Missing reference picture frames')
    return {'spliceFrame': splice, 'spliceSeconds': splice / FPS, 'lastOutputFrame': end - 1,
            'frames': [
                {'label': 'source-first', 'input': 'source', 'frame': 0},
                {'label': 'output-first', 'input': 'output', 'frame': 0},
                {'label': 'source-last', 'input': 'source', 'frame': source_video_frames - 1},
                {'label': 'output-source-last', 'input': 'output', 'frame': splice - 1},
                {'label': 'cta-first', 'input': 'cta', 'frame': 0},
                {'label': 'output-cta-first', 'input': 'output', 'frame': splice},
                {'label': 'cta-last', 'input': 'cta', 'frame': cta_video_frames - 1},
                {'label': 'output-last', 'input': 'output', 'frame': end - 1}],
            'audio': [
                {'label': 'source-ending', 'input': 'source', 'start': max(0, source_segment['audioSeconds'] - 3), 'seconds': min(3, source_segment['audioSeconds'])},
                {'label': 'cta-opening', 'input': 'cta', 'start': 0, 'seconds': min(3, cta_segment['audioSeconds'])},
                {'label': 'output-splice', 'input': 'output', 'start': max(0, splice / FPS - 3), 'seconds': min(3, splice / FPS) + min(3, cta_segment['seconds'])},
                {'label': 'output-ending', 'input': 'output', 'start': max(0, end / FPS - 3), 'seconds': min(3, end / FPS)}]}


def projection(total_seconds, benchmark_seconds, wall_seconds, output_bytes, encoder='unknown'):
    if min(total_seconds, benchmark_seconds, wall_seconds, output_bytes) <= 0:
        raise ValueError('Projection requires positive observed duration, wall time and bytes')
    return {'encoder': encoder, 'observedOutputBytes': output_bytes,
            'observedMbps': output_bytes * 8 / benchmark_seconds / 1e6,
            'reference12MbpsPlusAudioBytes': math.ceil(benchmark_seconds * 12_192_000 / 8),
            'estimatedAllOutputBytes': math.ceil(output_bytes / benchmark_seconds * total_seconds),
            'estimatedAllWallSeconds': total_seconds / benchmark_seconds * wall_seconds,
            'observedRealtimeSpeed': benchmark_seconds / wall_seconds,
            'caution': 'One pair is a sample, not a guaranteed bulk bitrate or speed. 12 Mbps is a target-rate planning reference, not a hard codec size bound.'}


def admission(free_bytes, projected_bytes):
    if free_bytes - projected_bytes < MIN_FREE_BYTES:
        raise ValueError('Projected exports would breach the 50 GiB free-space floor')


def judgement_candidate(result, manifest_sha256, artifacts):
    """Exact-version handoff only: an independent reviewer must decide all three checks."""
    if not re.fullmatch('[a-f0-9]{64}', manifest_sha256):
        raise ValueError('Invalid manifest hash for review handoff')
    for item in artifacts:
        if Path(item['path']).is_absolute() or '..' in Path(item['path']).parts \
                or not re.fullmatch('[a-f0-9]{64}', item['sha256']):
            raise ValueError('Review evidence must have a portable path and exact hash')
    return {'output_sha256': result['outputSha256'], 'source_sha256': result['sourceSha256'],
            'cta_sha256': result['ctaSha256'], 'manifest_sha256': manifest_sha256,
            'reviewer': '', 'rationale': '', 'evidence': artifacts,
            'checks': {'complete_source_preserved': None, 'cta_join_verified': None,
                       'picture_and_audio_verified': None},
            'mechanical_status': result['status'], 'qualityApproved': False,
            'instructions': 'Inspect the retained evidence, author independent rationale and decide every check. Save a separate .blend-verification.json only after judgement. This candidate is not approval.'}


def save_new(path, value):
    # Never overwrite a judgement/evidence file. Interrupted JSON fails closed.
    with path.open('x') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)
        handle.write('\n')


def probe(path, count=False):
    studio()
    args = ['ffprobe', '-v', 'error', '-threads', '2'] + (['-count_frames'] if count else [])
    result = subprocess.run(args + ['-show_streams', '-show_format', '-of', 'json', str(path)],
                            check=True, text=True, capture_output=True)
    if result.stderr.strip():
        raise ValueError('ffprobe reported a decoding error: ' + result.stderr.strip()[:300])
    return json.loads(result.stdout)


def decoded_samples(path):
    studio()
    # Stream decoded PCM to a byte counter: no huge temporary WAV and no local media work.
    process = subprocess.Popen(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
        '-err_detect', 'explode', '-threads', '2', '-i', str(path), '-map', '0:a:0', '-vn', '-ac', '2',
        '-ar', str(RATE), '-c:a', 'pcm_s16le', '-f', 's16le', '-'], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE)
    length = 0
    while chunk := process.stdout.read(1024 * 1024):
        length += len(chunk)
    error = process.stderr.read()
    if process.wait() or error.strip():
        raise ValueError('Audio decode failed: ' + error.decode(errors='replace')[:300])
    if length % 4:
        raise ValueError('Partial decoded PCM sample')
    return length // 4


def evidence(plan, paths, folder):
    studio()
    ancestor = folder.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    admission(shutil.disk_usage(ancestor).free, 64 * 1024**2)
    folder.mkdir(parents=True, exist_ok=False)
    artifacts = []
    for item in plan['frames']:
        target = folder / (item['label'] + '.png')
        subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
            '-threads', '2', '-i', str(paths[item['input']]), '-vf', f"select=eq(n\\,{item['frame']})",
            '-frames:v', '1', '-fps_mode', 'vfr', str(target)], check=True)
        if not target.is_file() or target.stat().st_size == 0:
            raise ValueError('Boundary frame missing: ' + item['label'])
        artifacts.append({'path': str(target), 'sha256': digest(target), **item})
    for item in plan['audio']:
        target = folder / (item['label'] + '.wav')
        subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-xerror',
            '-threads', '2', '-i', str(paths[item['input']]), '-ss', str(item['start']), '-t', str(item['seconds']),
            '-vn', '-c:a', 'pcm_s16le', '-ar', str(RATE), '-ac', '2', str(target)], check=True)
        artifacts.append({'path': str(target), 'sha256': digest(target), **item})
    return artifacts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--id', default='maths.algebra.use-term-rule')
    parser.add_argument('--aspect', choices=['wide', 'tall', 'both'], default='both')
    parser.add_argument('--source-root', type=Path, default=APP / 'inputs/teaching')
    parser.add_argument('--cta-root', type=Path, default=APP / 'out')
    parser.add_argument('--output-root', type=Path, default=APP / 'out/teaching')
    parser.add_argument('--verify', action='store_true')
    parser.add_argument('--boundary-evidence', type=Path)
    parser.add_argument('--benchmark-wall-seconds', type=float)
    parser.add_argument('--judgement-candidates', action='store_true',
                        help='Write mechanical evidence and pending reviewer templates; never final approval')
    parser.add_argument('--total-output-seconds', type=float,
                        help='Studio preflight sum of rounded sources plus their assigned CTA durations')
    args = parser.parse_args()
    manifest = read(APP / 'data/teaching-social-inputs.json')
    assignments = read(APP / 'data/teaching-cta-assignments.json')
    row = next((r for r in manifest['videos'] if r['skillKey'] == args.id), None)
    if row is None:
        parser.error('Unknown teaching skill ID')
    cta_id = assignments[args.id]
    cta = next(c for c in manifest['ctaLibrary'] if c['id'] == cta_id)
    if cta['audience'] not in ['both', 'parents']:
        parser.error('Teaching ending must be family-compatible')
    aspects = ['wide', 'tall'] if args.aspect == 'both' else [args.aspect]
    if not args.verify:
        print(json.dumps({'status': 'planned-no-media-opened', 'id': args.id, 'ctaId': cta_id,
                          'aspects': aspects, 'requires': 'Orchestrator verifies current CTA approval before any blend. No approval inferred by this helper.'}, indent=2))
        return
    studio()
    results = []
    for aspect in aspects:
        source = args.source_root / args.id / f'{aspect}.mp4'
        cta_path = args.cta_root / f'{cta_id}-{aspect}.mp4'
        output = args.output_root / f'{args.id}-{aspect}-cta.mp4'
        receipt = read(output.with_suffix('.receipt.json'))
        hashes = check_provenance(source, cta_path, output, row['media'][aspect], cta_id,
                                  aspect, receipt, read(cta_path.with_suffix('.render-receipt.json')))
        source_meta, cta_meta = probe(source), probe(cta_path)
        source_seg, cta_seg = segment(source_meta, aspect), segment(cta_meta, aspect)
        source_audio_samples, cta_audio_samples = decoded_samples(source), decoded_samples(cta_path)
        source_capacity, cta_capacity = source_seg['frames'] * (RATE // FPS), cta_seg['frames'] * (RATE // FPS)
        if source_audio_samples > source_capacity or cta_audio_samples > cta_capacity:
            raise ValueError('Rounded input segment would truncate actual decoded original audio')
        technical = check_output(probe(output, count=True), aspect,
                                 source_seg['frames'] + cta_seg['frames'], decoded_samples(output),
                                 source_capacity + cta_audio_samples, mp4_audio_timing(output))
        technical['decodedSourceAudioSamples'] = source_audio_samples
        technical['decodedCtaAudioSamples'] = cta_audio_samples
        plan = boundary_plan(source_seg, cta_seg, int(streams(source_meta, aspect)[0]['nb_frames']),
                             int(streams(cta_meta, aspect)[0]['nb_frames']))
        artifacts = evidence(plan, {'source': source, 'cta': cta_path, 'output': output},
                             args.boundary_evidence / aspect) if args.boundary_evidence else []
        # Inputs/output must remain stable throughout decode and evidence generation.
        check_provenance(source, cta_path, output, row['media'][aspect], cta_id,
                         aspect, receipt, read(cta_path.with_suffix('.render-receipt.json')))
        results.append({'aspect': aspect, 'status': 'technical-pass-quality-review-pending',
                        **hashes, **technical, 'bytes': output.stat().st_size,
                        'boundaryPlan': plan, 'boundaryArtifacts': artifacts})
    manifest_hash = digest(APP / 'data/teaching-social-inputs.json')
    report = {'id': args.id, 'ctaId': cta_id, 'host': socket.gethostname(), 'outputs': results,
              'manifestSha256': manifest_hash, 'verifierSha256': digest(Path(__file__)), 'qualityApproved': False}
    if args.judgement_candidates:
        for result in results:
            output = args.output_root / f"{args.id}-{result['aspect']}-cta.mp4"
            mechanical_path = output.with_suffix('.mechanical-verification.json')
            candidate_path = output.with_suffix('.blend-verification.candidate.json')
            retained = {**result, 'manifestSha256': manifest_hash, 'verifierSha256': digest(Path(__file__)), 'qualityApproved': False}
            save_new(mechanical_path, retained)
            artifacts = [{'path': str(mechanical_path.resolve().relative_to(APP)), 'sha256': digest(mechanical_path)}]
            for artifact in result['boundaryArtifacts']:
                artifacts.append({'path': str(Path(artifact['path']).resolve().relative_to(APP)), 'sha256': artifact['sha256']})
            save_new(candidate_path, judgement_candidate(result, manifest_hash, artifacts))
            result['mechanicalEvidencePath'] = str(mechanical_path)
            result['judgementCandidatePath'] = str(candidate_path)

    if args.benchmark_wall_seconds or args.total_output_seconds:
        if not args.benchmark_wall_seconds or not args.total_output_seconds:
            parser.error('Supply both benchmark wall seconds and total output seconds')
        estimate = projection(args.total_output_seconds, sum(r['expectedSeconds'] for r in results),
                              args.benchmark_wall_seconds, sum(r['bytes'] for r in results),
                              receipt['dependencies']['encoder'])
        admission(shutil.disk_usage(args.output_root).free, estimate['estimatedAllOutputBytes'])
        report['projection'] = estimate
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError, KeyError) as error:
        sys.exit(str(error))
