# Objective frame-fill check for tall renders: per second, how much of the 9:16 content rect
# (y 180-1180) holds non-background pixels. Prints runs of >= 2 s below the threshold.
# Usage: python3 -I scripts/fill-check.py out/<id>-tall.mp4 [...] [--min 0.7]
import subprocess, sys
import numpy as np

args = [a for a in sys.argv[1:] if not a.startswith('--')]
MIN = float(sys.argv[sys.argv.index('--min') + 1]) if '--min' in sys.argv else 0.7
if '--min' in sys.argv: args.remove(sys.argv[sys.argv.index('--min') + 1])
BG = np.array([0xf0, 0xf4, 0xff])
for f in args:
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', f, '-vf', 'fps=2,crop=1080:1000:0:180', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'], capture_output=True, check=True).stdout
    fr = np.frombuffer(raw, np.uint8).reshape(-1, 1000, 1080, 3)
    fill = []
    for im in fr:
        im = im.copy()
        im[900:, 600:] = BG  # the avatar circle's top edge pokes into the rect; ignore it
        rows = np.where((np.abs(im.astype(int) - BG).sum(2) > 40).sum(1) > 6)[0]
        fill.append(0 if len(rows) == 0 else (rows[-1] - rows[0]) / 1000)
    fill = np.array(fill)
    # Ignore the hook card (first 3 s) and the end card (last ~6 s).
    body = fill[6:-12]
    low = body < MIN
    runs, start = [], None
    for i, v in enumerate(list(low) + [False]):
        if v and start is None: start = i
        if not v and start is not None:
            if i - start >= 4: runs.append(f'{(start + 6) / 2:.1f}-{(i + 6) / 2:.1f}s ({body[start:i].mean():.0%})')
            start = None
    print(f"{f.split('/')[-1]:44s} median fill {np.median(body):.0%}  share under {MIN:.0%}: {low.mean():.0%}  {'; '.join(runs)}")
