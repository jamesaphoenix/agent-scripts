# Reach gate for the open frame: share of body frames (1 fps) whose content stops above y 1150 (tall)
# or left of x 1450 (wide), and the median reach. Target: tall median >= 1200 and short <= 35%; wide median >= 1500.
# gap: the median (over frames) of the tallest empty horizontal band between the first and last content rows in
# tall, and holes = share of frames with a gap over 300 px. A hero parked at the bottom under a headline at the
# top reaches far but leaves a hole. Targets: gap <= 220 and holes <= 25%.
# Usage: uv run -q --with numpy python -I scripts/extent.py <id> [...]
import json, os, re, subprocess, sys
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rows = {r['id']: r for r in json.load(open(f'{ROOT}/data/scripts.json'))}
def frames(p, W, H):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', p, '-vf', 'fps=1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(-1, H, W, 3).astype(int)
def ref(style, fmt, W, H):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', f'{ROOT}/data/stages/stage-{style}-{fmt}.png', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(H, W, 3).astype(int)
for vid in sys.argv[1:]:
    w = json.load(open(f'{ROOT}/data/words/{vid}.json'))
    end = int(w['durationMs'] / 1000)
    out = []
    for fmt, (W, H) in (('tall', (1080, 1920)), ('wide', (1920, 1080))):
        p = f'{ROOT}/out/{vid}-{fmt}.mp4'
        if not os.path.exists(p): out.append(f'{fmt}:missing'); continue
        fr = frames(p, W, H); R = ref(rows[vid].get('style', 'house'), fmt, W, H)
        body = fr[5:end]
        short = 0; ext = []; gaps = []
        for im in body:
            ink = np.abs(im - R).sum(-1) > 60
            if fmt == 'tall':
                rr = np.where(ink[180:1400, 60:950].sum(1) > 6)[0]
                e = 180 + rr[-1] if len(rr) else 0
                short += e < 1150
                gaps.append(int(np.diff(rr).max()) if len(rr) > 1 else 0)
            else:
                cc = np.where(ink[110:900, 96:1830].sum(0) > 6)[0]
                e = 96 + cc[-1] if len(cc) else 0
                short += e < 1450
            ext.append(e)
        g = f' gap {int(np.median(gaps))} holes {np.mean(np.array(gaps) > 300):.0%}' if gaps else ''
        out.append(f'{fmt}: short {short / max(1, len(body)):.0%} median-extent {int(np.median(ext))}{g}')
    print(vid, ' | '.join(out), flush=True)
