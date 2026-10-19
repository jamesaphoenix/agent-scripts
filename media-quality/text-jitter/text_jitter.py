#!/usr/bin/env python3
"""Offline text-motion review aid. Native frames, OCR discovery, explicit static-region gates.

No model/API requests. See README for coverage and intentional-animation limitations.
"""
import argparse
import collections
import csv
import hashlib
import html
import io
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

VERSION = 'text-jitter/v1'
cv2.setNumThreads(1)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as f:
        json.dump(value, f, indent=2, allow_nan=False)
        f.write('\n')
        temp = f.name
    os.replace(temp, path)


def probe(path):
    data = json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries',
        'stream=width,height,r_frame_rate,avg_frame_rate,duration,nb_frames:format=duration',
        '-of', 'json', str(path)], text=True))
    if not data.get('streams'):
        raise ValueError('No video stream')
    s = data['streams'][0]
    def rate(v):
        a, b = map(int, v.split('/'))
        return a / b if b else 0
    fps, avg = rate(s['r_frame_rate']), rate(s['avg_frame_rate'])
    if fps <= 0 or abs(fps - avg) > .001:
        raise ValueError('This harness requires constant-frame-rate input; normalise a separate copy first')
    duration = float(s.get('duration') or data['format']['duration'])
    if s.get('nb_frames') and abs(int(s['nb_frames']) / fps - duration) > max(.1, 2 / fps):
        raise ValueError('Video frame count and duration disagree')
    return {'width': int(s['width']), 'height': int(s['height']), 'fps': fps, 'duration': duration,
            'timing': 'native frame index / nominal CFR, r_frame_rate equals avg_frame_rate'}


def frames(path, info, start=0, end=None):
    """Stream one decoded grey frame at a time, preserving native frame cadence."""
    fps = info['fps']
    first = max(0, math.ceil(start * fps - 1e-7))
    last = math.ceil(min(end if end is not None else info['duration'], info['duration']) * fps - 1e-7)
    count = last - first
    if count <= 0:
        raise ValueError('Empty or out-of-range scan interval')
    cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-threads', '1', '-ss', str(first / fps), '-i', str(path),
           '-map', '0:v:0', '-an', '-sn', '-threads', '1', '-frames:v', str(count),
           '-fps_mode', 'passthrough', '-pix_fmt', 'gray', '-f', 'rawvideo', '-']
    size = info['width'] * info['height']
    with tempfile.TemporaryFile() as err:
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=err)
        try:
            for i in range(count):
                parts, remaining = [], size
                while remaining:
                    part = p.stdout.read(remaining)
                    if not part:
                        break
                    parts.append(part)
                    remaining -= len(part)
                if remaining:
                    raise ValueError(f'Truncated decode at frame {first + i}; expected {count} frames')
                frame = np.frombuffer(b''.join(parts), dtype=np.uint8).reshape(info['height'], info['width'])
                yield first + i, (first + i) / fps, frame
            if p.wait() != 0:
                err.seek(0)
                raise RuntimeError(err.read().decode(errors='replace')[-2000:])
        finally:
            p.stdout.close()
            if p.poll() is None:
                p.terminate()
                p.wait()


def checked_box(box, shape):
    if len(box) != 4 or any(not math.isfinite(float(v)) for v in box):
        raise ValueError('Box must contain finite x,y,width,height')
    x, y, w, h = map(int, box)
    H, W = shape[:2]
    if x < 0 or y < 0 or w < 8 or h < 8 or x + w > W or y + h > H:
        raise ValueError(f'Text box outside decoded frame: {box}, frame {W}x{H}')
    return x, y, w, h


def peak_offset(scores, x, y):
    def offset(a, b, c):
        den = a - 2 * b + c
        return float(np.clip(.5 * (a - c) / den, -.5, .5)) if abs(den) > 1e-8 else 0.
    dx = offset(*scores[y, x-1:x+2]) if 0 < x < scores.shape[1] - 1 else 0.
    dy = offset(*scores[y-1:y+2, x]) if 0 < y < scores.shape[0] - 1 else 0.
    return dx, dy


def measure(rows, box, *, text='', max_range_px=1.5, max_step_px=1.5,
            search_px=24, min_score=.82, min_seconds=.65):
    """Track a fixed glyph template; never use moving dark-pixel bounding boxes."""
    rows = list(rows)
    base = {'text': text, 'box': list(box), 'status': 'unscorable', 'samples': [],
            'start': rows[0][1] if rows else None, 'end': rows[-1][1] if rows else None}
    if len(rows) < 3 or rows[-1][1] - rows[0][1] < min_seconds:
        return {**base, 'reason': 'insufficient native-frame duration'}
    x, y, w, h = checked_box(box, rows[0][2].shape)
    ref = rows[len(rows) // 2][2][y:y+h, x:x+w].copy()
    base.update(start=rows[0][1], end=rows[-1][1], reference_frame=rows[len(rows)//2][0])
    if float(ref.std()) < 8:
        return {**base, 'reason': 'blank or insufficient glyph contrast'}
    H, W = rows[0][2].shape
    sx, sy = max(0, x-search_px), max(0, y-search_px)
    ex, ey = min(W, x+w+search_px), min(H, y+h+search_px)
    for index, seconds, image in rows:
        result = cv2.matchTemplate(image[sy:ey, sx:ex], ref, cv2.TM_CCOEFF_NORMED)
        _, score, _, loc = cv2.minMaxLoc(result)
        px, py = loc
        ox, oy = peak_offset(result, px, py)
        edge = (px in (0, result.shape[1]-1) or py in (0, result.shape[0]-1))
        base['samples'].append({'frame': index, 'seconds': round(seconds, 6),
                                'dx': round(sx + px + ox - x, 4),
                                'dy': round(sy + py + oy - y, 4),
                                'score': round(score, 5), 'valid': score >= min_score and not edge})
    valid = [s for s in base['samples'] if s['valid']]
    base['coverage'] = len(valid) / len(rows)
    if len(valid) != len(rows):
        # A single missing frame could be the worst defect. Do not silently discard it.
        return {**base, 'reason': 'glyph changed, disappeared, scaled, or left search window; inspect this interval'}
    xy = np.array([[s['dx'], s['dy']] for s in valid])
    span = np.ptp(xy, axis=0)
    steps = np.linalg.norm(np.diff(xy, axis=0), axis=1)
    extent = float(np.linalg.norm(span))
    max_step = float(steps.max(initial=0))
    finding = extent > max_range_px or max_step > max_step_px
    velocity = np.diff(xy, axis=0)
    reversals = 0
    for axis in range(2):
        meaningful = np.sign(velocity[np.abs(velocity[:, axis]) > .25, axis])
        reversals += int(np.sum(meaningful[1:] * meaningful[:-1] < 0))
    return {**base, 'status': 'finding' if finding else 'clear', 'range_xy_px': span.tolist(),
            'range_px': extent, 'max_step_px': max_step,
            'classification': ('abrupt_shift' if max_step > max_step_px else
                               'oscillation' if reversals >= 2 else 'continuous_drift') if finding else 'steady',
            'thresholds': {'range_px': max_range_px, 'step_px': max_step_px}}


def ocr(image):
    scale = min(1., 1400 / max(image.shape))
    small = cv2.resize(image, None, fx=scale, fy=scale) if scale < 1 else image
    ok, png = cv2.imencode('.png', small)
    if not ok:
        raise RuntimeError('Cannot encode OCR probe')
    p = subprocess.run(['tesseract', 'stdin', 'stdout', '--psm', '11', '-l', 'eng', 'tsv'],
                       input=png.tobytes(), capture_output=True, check=True,
                       env={**os.environ, 'OMP_THREAD_LIMIT': '1'})
    grouped = collections.defaultdict(list)
    for row in csv.DictReader(io.StringIO(p.stdout.decode()), delimiter='\t'):
        if int(row['level']) == 5 and float(row['conf']) >= 55 and row.get('text', '').strip():
            grouped[(row['block_num'], row['par_num'], row['line_num'])].append(row)
    result = []
    for words in grouped.values():
        text = ' '.join(w['text'].strip() for w in words)
        if sum(c.isalnum() for c in text) < 4:
            continue
        x = min(int(w['left']) for w in words) / scale
        y = min(int(w['top']) for w in words) / scale
        right = max(int(w['left']) + int(w['width']) for w in words) / scale
        bottom = max(int(w['top']) + int(w['height']) for w in words) / scale
        box = [max(0, int(x)-2), max(0, int(y)-2), int(right-x)+4, int(bottom-y)+4]
        box[2] = min(box[2], image.shape[1]-box[0])
        box[3] = min(box[3], image.shape[0]-box[1])
        if box[2] >= 8 and box[3] >= 8:
            result.append({'text': text, 'box': box})
    return result


def same_line(line, candidates, distance=48):
    return any(c['text'].casefold() == line['text'].casefold() and
               abs(c['box'][0]-line['box'][0]) <= distance and
               abs(c['box'][1]-line['box'][1]) <= distance for c in candidates)


def validate_exclusions(items):
    for ex in items:
        if not ex.get('reason', '').strip() or not 0 <= ex['start'] < ex['end']:
            raise ValueError('Every intentional-motion exclusion needs a time interval and written reason')


def excluded(items, start, end, box):
    x, y, w, h = box
    for ex in items:
        if ex['start'] <= start and ex['end'] >= end:
            if not ex.get('box'):
                return True
            a, b, c, d = ex['box']
            if a <= x and b <= y and a+c >= x+w and b+d >= y+h:
                return True
    return False


def analyse(path, info, config, start, end, settings):
    results, skipped = [], []
    exclusions = config.get('exclusions', [])
    validate_exclusions(exclusions)
    if config.get('regions'):
        for region in config['regions']:
            a, b = max(start, region['start']), min(end, region['end'])
            if a >= b:
                continue
            if excluded(exclusions, a, b, region['box']):
                skipped.append(region)
                continue
            # Bound memory even when a region is declared for an entire lesson.
            # Overlap prevents a jump at a window boundary from escaping both checks.
            window_start = a
            while window_start < b:
                window_end = min(b, window_start + 2)
                rows = list(frames(path, info, window_start, window_end))
                result = measure(rows, region['box'], text=region.get('text', region['name']),
                                 **{**settings, **{k: region[k] for k in settings if k in region}})
                results.append({**result, 'name': region['name'], 'mode': 'declared_static'})
                if window_end >= b:
                    break
                window_start += 1
    else:
        probe_step = max(1, round(.75 * info['fps']))
        buffer = collections.deque(maxlen=probe_step * 2 + 1)
        probes = collections.deque(maxlen=3)
        for index, seconds, image in frames(path, info, start, end):
            buffer.append((index, seconds, image))
            if index % probe_step:
                continue
            probes.append(ocr(image))
            if len(probes) < 3 or len(buffer) != buffer.maxlen:
                continue
            rows = list(buffer)
            # Three matching OCR probes exclude many text changes and scene cuts.
            # Skip a further 0.25s of the earliest probe to allow a text entrance.
            rows = rows[max(0, round(.25 * info['fps'])):]
            for line in probes[1]:
                if not same_line(line, probes[0]) or not same_line(line, probes[2]):
                    continue
                if excluded(exclusions, rows[0][1], rows[-1][1], line['box']):
                    skipped.append({'text': line['text'], 'start': rows[0][1], 'end': rows[-1][1]})
                    continue
                result = measure(rows, line['box'], text=line['text'], **settings)
                results.append({**result, 'mode': 'ocr_discovery'})
    return results, skipped


def encode_fixture(path, images, fps):
    H, W = images[0].shape[:2]
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'gray',
                          '-s', f'{W}x{H}', '-r', str(fps), '-i', '-', '-an', '-c:v', 'libx264',
                          '-threads', '1', '-crf', '18', '-pix_fmt', 'yuv420p', str(path)], stdin=subprocess.PIPE)
    try:
        for image in images:
            p.stdin.write(image.tobytes())
        p.stdin.close()
        if p.wait():
            raise RuntimeError('Fixture encoder failed')
    finally:
        if p.poll() is None:
            p.terminate()
            p.wait()


def merge_events(events):
    """Keep measurements intact but group overlapping findings for the evidence browser."""
    merged = []
    for event in sorted(events, key=lambda e: e.get('start', 0)):
        prior = next((p for p in merged if p['text'] == event['text'] and p['status'] == event['status']
                      and p.get('end', -1) >= event.get('start', 0)
                      and abs(p['box'][0]-event['box'][0]) < 48
                      and abs(p['box'][1]-event['box'][1]) < 48), None)
        if prior is None:
            merged.append({k: v for k, v in event.items() if k != 'samples'})
        else:
            prior['end'] = max(prior['end'], event['end'])
            for key in ('range_px', 'max_step_px'):
                if key in event:
                    prior[key] = max(prior.get(key, 0), event[key])
            prior['merged_windows'] = prior.get('merged_windows', 1) + 1
    return merged


def evidence(path, info, events, out, limit):
    saved = []
    for i, event in enumerate(events[:limit]):
        if shutil.disk_usage(out).free < 50 * 1024**3:
            raise RuntimeError('Evidence paused: less than 50 GiB free')
        a, b = max(0, event.get('start', 0)-.35), min(info['duration'], event.get('end', 0)+.35)
        target = out / f'finding-{i+1:03}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', str(a), '-i', str(path),
                        '-t', str(b-a), '-map', '0:v:0', '-map', '0:a:0?', '-c:v', 'libx264',
                        '-threads', '1', '-preset', 'fast', '-crf', '19', '-c:a', 'aac',
                        '-movflags', '+faststart', str(target)], check=True)
        event['evidence'] = {'path': target.name, 'sha256': digest(target), 'start': a, 'end': b}
        saved.append(target.name)
    return saved


def report_html(report, out):
    esc = lambda s: html.escape(str(s), quote=True)
    blocks = []
    for event in report['events']:
        ev = event.get('evidence')
        video = f'<video controls preload="none" src="{esc(ev["path"])}"></video>' if ev else ''
        blocks.append(f'<article><h2>{esc(event.get("start", "?"))}s: {esc(event["text"])}</h2>'
                      f'<p>{esc(event["status"])}: {esc(event.get("classification", event.get("reason")))}. '
                      f'Range {esc(round(event.get("range_px", 0), 2))} px; '
                      f'largest step {esc(round(event.get("max_step_px", 0), 2))} px.</p>{video}'
                      f'<details><summary>Measurements</summary><pre>{esc(json.dumps(event, indent=2))}</pre></details></article>')
    page = ('<!doctype html><meta charset="utf-8"><title>Text jitter review</title>'
            '<style>body{font:17px system-ui;max-width:980px;margin:40px auto;padding:0 20px;line-height:1.5}'
            'video{max-width:100%;max-height:680px}article{border-top:1px solid #bbb;margin:24px 0}pre{overflow:auto}</style>'
            f'<h1>Text jitter: {esc(report["status"])}</h1><p>{esc(report["video"])}</p>'
            '<p>This is a motion review aid, not complete visual or launch approval. '
            'Intended animation needs an explicit reviewed exclusion. Unscorable intervals are not passes.</p>'
            f'<p>{esc(report["summary"])}. Full measurements: <a href="report.json">report.json</a>.</p>'
            + ''.join(blocks))
    (out / 'index.html').write_text(page)


def scan(path, out, config=None, start=0, end=None, evidence_limit=12, expected_sha256=None, **settings):
    path, out = Path(path).resolve(), Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output directory must be new or empty; retain previous reports')
    out.mkdir(parents=True, exist_ok=True)
    info, before = probe(path), digest(path)
    if expected_sha256 is not None and before != expected_sha256:
        raise ValueError('Source SHA256 differs from the requested delivery manifest')
    end = info['duration'] if end is None else min(end, info['duration'])
    settings = {'max_range_px': 1.5, 'max_step_px': 1.5, 'search_px': 24, 'min_score': .82, **settings}
    started = time.time()
    results, skipped = analyse(path, info, config or {}, start, end, settings)
    events = merge_events([r for r in results if r['status'] != 'clear'])
    counts = dict(collections.Counter(r['status'] for r in results))
    status = 'needs_review' if events else 'no_findings_in_tracked_regions' if results else 'insufficient_coverage'
    report = {'schema': VERSION, 'video': str(path), 'source_sha256': before,
              'implementation_sha256': digest(__file__), 'config': config or {}, 'settings': settings,
              'dependencies': {'opencv': cv2.__version__, 'numpy': np.__version__, 'python': sys.version.split()[0]},
              'info': info, 'scan_range': [start, end], 'status': status, 'summary': counts,
              'tracked_windows': len(results), 'events': events, 'all_measurements': results,
              'exclusions_applied': skipped, 'api_requests': 0,
              'coverage_note': 'Native frames of declared regions, or OCR lines recognised across three probes. '
                               'Unrecognised, short-lived and changed text may not be covered. No whole-video approval.'}
    atomic_json(out / 'report.json', report)
    evidence(path, info, events, out, evidence_limit)
    report['evidence_events_omitted'] = max(0, len(events)-evidence_limit)
    report['elapsed_seconds'] = round(time.time()-started, 2)
    if digest(path) != before:
        report['status'] = 'invalid_source_changed'
        atomic_json(out / 'report.json', report)
        raise RuntimeError('Source changed during scan; report is invalid')
    atomic_json(out / 'report.json', report)
    report_html(report, out)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('video', nargs='?', type=Path)
    p.add_argument('--manifest', type=Path, help='Delivery manifest with videos[] path/id/aspect, or scan jobs[]')
    p.add_argument('--root', type=Path, help='Resolve manifest video paths here (default manifest parent)')
    p.add_argument('--regions', type=Path, help='Reviewed static regions and intentional-motion exclusions JSON')
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--start', type=float, default=0)
    p.add_argument('--end', type=float)
    p.add_argument('--range-px', type=float, default=1.5)
    p.add_argument('--step-px', type=float, default=1.5)
    p.add_argument('--search-px', type=int, default=24)
    p.add_argument('--evidence-limit', type=int, default=12)
    p.add_argument('--limit', type=int, help='Limit batch jobs, for pilot scans')
    a = p.parse_args()
    if bool(a.video) == bool(a.manifest):
        p.error('Choose one video or --manifest')
    if min(a.range_px, a.step_px, a.search_px) <= 0 or a.start < 0 or a.evidence_limit < 0 or (
            a.limit is not None and a.limit <= 0):
        p.error('Thresholds must be positive; start/evidence limit must be nonnegative')
    os.nice(10)
    config = json.loads(a.regions.read_text()) if a.regions else {}
    kwargs = dict(start=a.start, end=a.end, evidence_limit=a.evidence_limit,
                  max_range_px=a.range_px, max_step_px=a.step_px, search_px=a.search_px)
    if a.video:
        r = scan(a.video, a.out, config, **kwargs)
        print(json.dumps({k: r[k] for k in ('status', 'summary', 'tracked_windows', 'elapsed_seconds')}))
        return 1 if r['events'] else 0 if r['tracked_windows'] else 2
    manifest = json.loads(a.manifest.read_text())
    jobs = manifest.get('jobs', manifest.get('videos', []))
    root = a.root or a.manifest.resolve().parent
    if a.limit is not None:
        jobs = jobs[:a.limit]
    if not jobs:
        raise ValueError('No batch jobs')
    if a.out.exists() and any(a.out.iterdir()):
        raise ValueError('Batch output directory must be new or empty')
    a.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for i, job in enumerate(jobs):
        label = f'{i+1:04}'
        try:
            r = scan(root / job['path'], a.out / label, job.get('config', config),
                     expected_sha256=job.get('sha256'), **kwargs)
            rows.append({'id': job.get('id', job['path']), 'aspect': job.get('aspect'),
                         'status': r['status'], 'summary': r['summary'], 'report': f'{label}/index.html'})
        except Exception as exc:
            rows.append({'id': job.get('id', job['path']), 'status': 'error', 'error': str(exc)})
        atomic_json(a.out / 'index.json', {'schema': VERSION, 'complete': i+1 == len(jobs), 'jobs': rows})
        print(json.dumps(rows[-1]), flush=True)
    items = ''.join(f'<li>{html.escape(str(r["id"]))}: {html.escape(r["status"])} '
                    + (f'<a href="{r["report"]}">review</a>' if r.get('report') else html.escape(r['error']))
                    + '</li>' for r in rows)
    (a.out / 'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>Batch text-motion review</h1><ul>'+items+'</ul>')
    return 2 if any(r['status'] in ('error', 'insufficient_coverage') for r in rows) else 1 if any(
        r['status'] == 'needs_review' for r in rows) else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, RuntimeError, subprocess.SubprocessError, OSError) as e:
        print(f'text-jitter: {e}', file=sys.stderr)
        sys.exit(2)
