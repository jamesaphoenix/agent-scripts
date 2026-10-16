# Small-text gate for tall renders: OCR one frame per second of the body (after the hook card,
# before the end card), inside the content rect only, and flag words whose box height suggests
# a font under ~40 px. Word box height ~ 0.72 x font size for lowercase-only words (x-height + ascender),
# so we use the tallest box per line as the line's size.
# Usage: uv run -q --with numpy python -I scripts/text-size.py <id> [...]
import json, os, re, subprocess, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for vid in sys.argv[1:]:
    w = json.load(open(f'{ROOT}/data/words/{vid}.json'))
    end = (w['durationMs'] + 300) / 1000 - 0.5
    p = f'{ROOT}/out/{vid}-tall.mp4'
    small, lines_total = [], 0
    with tempfile.TemporaryDirectory() as d:
        subprocess.run(['ffmpeg', '-v', 'error', '-ss', '5', '-to', f'{end:.2f}', '-i', p, '-vf', 'fps=1,crop=900:980:50:180', f'{d}/f%03d.png'], check=True)
        for f in sorted(os.listdir(d)):
            t = 5 + int(f[1:4]) - 1
            tsv = subprocess.run(['tesseract', f'{d}/{f}', '-', '--psm', '11', 'tsv'], capture_output=True, text=True).stdout.splitlines()[1:]
            lines = {}
            for row in tsv:
                c = row.split('\t')
                if len(c) < 12 or not re.search(r'[A-Za-z]{3,}', c[11]) or float(c[10]) < 70: continue
                key = (c[2], c[3], c[4])
                lines.setdefault(key, []).append((int(c[9]), c[11]))
            for ws in lines.values():
                text = ' '.join(x[1] for x in ws)
                if text.upper() == text: continue  # all-caps kickers are the house 36 px label style
                h = max(x[0] for x in ws); lines_total += 1
                # Mixed-case word boxes span ascender to descender (~0.95 x font size): 34 px box ~ 36 px font.
                if h < 34: small.append((t, h, ' '.join(x[1] for x in ws)[:40]))
    share = len(small) / max(1, lines_total)
    ex = '; '.join(f'{t}s "{s}" ({h}px box)' for t, h, s in small[:5])
    print(f"{vid}: {'OK' if share < 0.08 else 'SMALL TEXT'} {share:.0%} of text lines look under ~40 px. {ex}", flush=True)
