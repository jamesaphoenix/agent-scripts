# Adversarial Gemini critic over a finished render (picture + sound together).
# Usage: python3 -I scripts/critic.py out/<id>-tall.mp4 [...]   -> out/review/<file>.critic.json
# Checks are booleans with timestamped evidence, not scores: a reviewer must name what it saw.
import json, os, subprocess, sys, time, urllib.request

KEY = subprocess.run(['op', 'read', 'op://api-keys/Gemini/api_key'], capture_output=True, text=True, check=True).stdout.strip()
MODEL = 'gemini-3.1-pro-preview'
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = {s['id']: s for s in json.load(open(os.path.join(ROOT, 'data/scripts.json')))}

PROMPT = '''You are a strict, adversarial reviewer of a short founder-led social video about the UK 11+ exam.
Your job is to FIND PROBLEMS, not to praise. The narrator is an AI voice clone of the founder, James; the
circle at the bottom right is an AI lip-synced avatar of him. The approved script is:

"""{script}"""

Format: {fmt}. {safe}

Check each item and answer strictly as JSON:
{{
 "narration_matches_script": bool, "narration_issues": [ {{"t": "mm:ss", "issue": str}} ],
 "audio_glitches": [ {{"t": "mm:ss", "issue": str}} ],
 "lip_sync_ok": bool, "avatar_issues": [ {{"t": "mm:ss", "issue": str}} ],
 "captions_match_speech": bool, "caption_issues": [ {{"t": "mm:ss", "issue": str}} ],
 "visuals_land_on_cue": bool, "cue_issues": [ {{"t": "mm:ss", "issue": "a visual that appears before or long after the narration names it"}} ],
 "text_problems": [ {{"t": "mm:ss", "issue": "overlap, clipping, cut-off words, text off-screen or over the avatar/captions, typos, em dashes, American spelling"}} ],
 "brand_name_visible_or_spoken": bool,
 "factual_or_tone_concerns": [ str ],
 "empty_or_weak_frames": [ {{"t": "mm:ss", "issue": "large empty areas, tiny unreadable text, a scene that adds nothing"}} ],
 "hook_strength_first_3s": "strong|ok|weak", "hook_note": str,
 "top_fixes": [ str ],
 "verdict": "ship|fix"
}}
Only report a problem you can point to with a timestamp. Use "fix" if anything a viewer would notice is wrong.'''


def upload(path):
    size = os.path.getsize(path)
    req = urllib.request.Request(
        f'https://generativelanguage.googleapis.com/upload/v1beta/files?key={KEY}',
        data=json.dumps({'file': {}}).encode(),
        headers={'X-Goog-Upload-Protocol': 'resumable', 'X-Goog-Upload-Command': 'start', 'X-Goog-Upload-Header-Content-Length': str(size), 'X-Goog-Upload-Header-Content-Type': 'video/mp4', 'Content-Type': 'application/json'},
    )
    url = urllib.request.urlopen(req).headers['X-Goog-Upload-URL']
    up = urllib.request.Request(url, data=open(path, 'rb').read(), headers={'X-Goog-Upload-Offset': '0', 'X-Goog-Upload-Command': 'upload, finalize'}, method='POST')
    f = json.load(urllib.request.urlopen(up))['file']
    while f.get('state') == 'PROCESSING':
        time.sleep(4)
        f = json.load(urllib.request.urlopen(f"https://generativelanguage.googleapis.com/v1beta/{f['name']}?key={KEY}"))
    if f.get('state') != 'ACTIVE':
        raise RuntimeError(f'upload failed: {f}')
    return f


def review(path):
    base = os.path.basename(path)
    vid = base.rsplit('-', 1)[0]
    tall = base.endswith('-tall.mp4')
    fmt = '9:16 vertical for TikTok/Reels/Shorts' if tall else '16:9 for LinkedIn and X'
    safe = 'TikTok UI covers the right edge (x > 950 of 1080) and the bottom (y > 1575 of 1920): flag anything important there.' if tall else ''
    f = upload(path)
    body = {
        'contents': [{'parts': [{'file_data': {'mime_type': 'video/mp4', 'file_uri': f['uri']}, 'video_metadata': {'fps': 4}}, {'text': PROMPT.format(script=SCRIPTS[vid]['script'], fmt=fmt, safe=safe)}]}],
        'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0},
    }
    req = urllib.request.Request(f'https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent?key={KEY}', data=json.dumps(body).encode(), headers={'content-type': 'application/json'})
    d = json.load(urllib.request.urlopen(req, timeout=600))
    text = d['candidates'][0]['content']['parts'][0]['text']
    os.makedirs(os.path.join(ROOT, 'out/review'), exist_ok=True)
    out = os.path.join(ROOT, 'out/review', base.replace('.mp4', '.critic.json'))
    open(out, 'w').write(text)
    v = json.loads(text)
    v = v[0] if isinstance(v, list) else v
    print(base, v.get('verdict'), '|', '; '.join(v.get('top_fixes', []))[:400])


for p in sys.argv[1:]:
    try:
        review(p)
    except Exception as e:
        print(os.path.basename(p), 'ERROR', str(e)[:300])
