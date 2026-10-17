# Lead QA over finished renders. For each <id>-{tall,wide}.mp4 it checks:
#   duration matches the film's expected length (narration + CTA timing), audio present at a sane level,
#   no scene pixels spilling into the gutters around the content rect (where the avatar and captions live),
#   and (tall) how much of the content rect the scenes fill.
# Usage: uv run -q --with numpy python -I scripts/qa.py <id> [...]
import json, os, re, subprocess, sys
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BG = np.array([0xf0, 0xf4, 0xff])
FPS = 30
ctas = {c['id']: c for c in json.load(open(f'{ROOT}/data/ctas.json'))}
rows = {r['id']: r for r in json.load(open(f'{ROOT}/data/scripts.json'))}
assign = json.load(open(f'{ROOT}/data/cta-assignments.json'))


def expected_s(vid):
    w = json.load(open(f'{ROOT}/data/words/{vid}.json'))
    r = rows[vid]
    cid = assign[vid]
    cw = json.load(open(f'{ROOT}/data/words/{cid}.json'))
    f = lambda ms: round(ms / 1000 * FPS)
    start = w['durationMs'] + 300
    return f(start + 600 + cw['durationMs'] + 1700) / FPS, (w['durationMs'] + 300) / 1000


def frames(path, W, H):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', path, '-vf', 'fps=2', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3)


# Gutters that scene content must stay out of, as (y0, y1, x0, x1).
REGIONS = {
    # tall: under the content rect (avatar, captions), TikTok's right-hand button column, above the rect
    ('tall', False): [(1163, 1168, 60, 690), (190, 1150, 958, 1075), (140, 168, 40, 950)],
    # tall with no circle: content may run to y 1300, but not into the caption band or the button column
    ('tall', True): [(1395, 1405, 60, 950), (190, 1390, 958, 1075), (140, 168, 40, 950)],
    # wide: above the caption band, the strip left of the avatar, above the content rect
    ('wide', False): [(905, 925, 96, 1500), (690, 925, 1525, 1560), (86, 104, 0, 1920)],
    ('wide', True): [(905, 925, 96, 1820), (86, 104, 0, 1920)],
}

REF = {}


def stage(style, fmt, W, H):
    # The empty stage for this look (render once: render.ts stills stage-<look>-<fmt> 0 -> data/stages/).
    k = (style, fmt)
    if k not in REF:
        f = f'{ROOT}/data/stages/stage-{style}-{fmt}.png'
        if not os.path.exists(f):
            subprocess.run(['node', '--import', 'tsx', 'scripts/render.ts', 'stills', f'stage-{style}-{fmt}', '0'], cwd=ROOT, check=True, capture_output=True)
            os.makedirs(f'{ROOT}/data/stages', exist_ok=True)
            os.replace(f'{ROOT}/out/stills/stage-{style}-{fmt}-0.png', f)
        raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', f, '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
        REF[k] = np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)
    return REF[k]


CUR = {}


def ink(a, t=60, at=None):
    # `at` = (y0, x0) of the slice inside the frame, so it is compared with the same pixels of the empty stage.
    y0, x0 = at if at else (0, 0)
    ref = CUR['ref'][y0:y0 + a.shape[0], x0:x0 + a.shape[1]]
    return (np.abs(a.astype(int) - ref).sum(-1) > t)


def solid(a, at=None):
    # Real content (text, fills, strokes), not soft card shadows.
    return ink(a, 150, at)


for vid in sys.argv[1:]:
    exp, cta_start = expected_s(vid)
    for fmt, (W, H) in (('tall', (1080, 1920)), ('wide', (1920, 1080))):
        p = f'{ROOT}/out/{vid}-{fmt}.mp4'
        if not os.path.exists(p):
            print(f'{vid}-{fmt}: MISSING'); continue
        dur = float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', p], capture_output=True, text=True).stdout)
        vol = subprocess.run(['ffmpeg', '-i', p, '-af', 'volumedetect', '-f', 'null', '-'], capture_output=True, text=True).stderr
        mean = float(re.search(r'mean_volume: (-?[\d.]+)', vol).group(1)) if 'mean_volume' in vol else None
        fr = frames(p, W, H)
        CUR['ref'] = stage(rows[vid].get('style', 'house'), fmt, W, H)
        nocircle = rows[vid].get('avatar') == 'none'
        body = range(int(4.8 * 2), int(cta_start * 2) - 1)  # after the hook card and name tag, before the end card
        spills, fills = [], []
        for i in body:
            im = fr[i]
            regions = REGIONS[(fmt, nocircle)]
            gut = sum(solid(im[y0:y1, x0:x1], (y0, x0)).sum() for y0, y1, x0, x1 in regions)
            if fmt == 'tall':
                if nocircle:
                    # Open layout: content may run to y 1300; the name tag only shows during the hook.
                    c = im[180:1300]
                    rr = np.where(ink(c, 60, (180, 0)).sum(1) > 6)[0]
                    fills.append(0 if len(rr) == 0 else (rr[-1] - rr[0]) / 1120)
                else:
                    c = im[180:1160].copy(); c[900:, 600:] = CUR['ref'][1080:1160, 600:]
                    rr = np.where(ink(c, 60, (180, 0)).sum(1) > 6)[0]
                    fills.append(0 if len(rr) == 0 else (rr[-1] - rr[0]) / 980)
            if gut > 300:  # a few px of an edge or stroke is fine; flag real intrusions
                boxes = []
                for y0, y1, x0, x1 in regions:
                    ys, xs = np.where(solid(im[y0:y1, x0:x1], (y0, x0)))
                    if len(ys) > 10: boxes.append(f'x{xs.min() + x0}-{xs.max() + x0} y{ys.min() + y0}-{ys.max() + y0}')
                spills.append(f"{i / 2:.1f}s [{', '.join(boxes)}]")
        issues = []
        if abs(dur - exp) > 0.15: issues.append(f'duration {dur:.2f}s vs {exp:.2f}s')
        if mean is None or not (-26 < mean < -12): issues.append(f'audio mean {mean} dB')
        if spills: issues.append(f"gutter spill in {len(spills)} samples, e.g. {'; '.join(spills[:3])}")
        extra = f'  fill median {np.median(fills):.0%}, under 60% for {np.mean(np.array(fills) < 0.6):.0%} of the body' if fills else ''
        print(f"{vid}-{fmt}: {'OK' if not issues else 'CHECK ' + '; '.join(issues)}{extra}", flush=True)
