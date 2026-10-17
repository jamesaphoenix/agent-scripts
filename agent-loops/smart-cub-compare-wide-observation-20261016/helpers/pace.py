# Slows narration by a pace factor without changing pitch, and keeps every timing in step.
#
# James chose 7.5% slower on 2026-10-13 (1.075) after testing 12.5% (too slow). Scenes and cues follow the
# word timings, so stretching the audio and scaling data/words slows the whole film. The narration itself is
# the same take, so its delivery and the voice match are unchanged.
#
# The first run on an id keeps the untouched files as raw copies, and every run rebuilds from them, so it is
# safe to re-run with a different factor:
#   public/audio/raw/<id>.mp3, data/words/raw/<id>.json, public/avatar/raw/<id>.mp4
# A words file without "pace" is raw (fresh from tts.ts), so re-voicing an id and running this again picks up
# the new take. Order for a new video: tts.ts -> avatar.ts -> pace.py. If you regenerate a lip-sync clip
# later, delete public/avatar/raw/<id>.mp4 first so the new clip becomes the raw one.
#
# Usage: python3 -I scripts/pace.py [--pace 1.075] [id ...]   (no ids = every words file, CTAs included)
import json, os, shutil, subprocess, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
args = sys.argv[1:]
pace = 1.075
if '--pace' in args:
    i = args.index('--pace'); pace = float(args[i + 1]); del args[i:i + 2]
ids = args or sorted(f[:-5] for f in os.listdir(f'{ROOT}/data/words') if f.endswith('.json'))
P = lambda *p: os.path.join(ROOT, *p)
run = lambda *c: subprocess.run(c, check=True, capture_output=True, text=True).stdout
dur = lambda f: float(run('ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', f))

for d in ('public/audio/raw', 'data/words/raw', 'public/avatar/raw'):
    os.makedirs(P(d), exist_ok=True)

for vid in ids:
    words, audio, clip = P(f'data/words/{vid}.json'), P(f'public/audio/{vid}.mp3'), P(f'public/avatar/{vid}.mp4')
    raw_words, raw_audio, raw_clip = P(f'data/words/raw/{vid}.json'), P(f'public/audio/raw/{vid}.mp3'), P(f'public/avatar/raw/{vid}.mp4')
    if 'pace' not in json.load(open(words)):
        shutil.copy2(words, raw_words); shutil.copy2(audio, raw_audio)
    if os.path.exists(clip) and not os.path.exists(raw_clip):
        shutil.copy2(clip, raw_clip)
    w = json.load(open(raw_words))
    tmp = P(f'public/audio/raw/{vid}.paced.wav')
    run('sox', raw_audio, tmp, 'tempo', '-s', f'{1 / pace:.6f}')
    run('ffmpeg', '-v', 'error', '-y', '-i', tmp, '-c:a', 'libmp3lame', '-b:a', '192k', audio)
    os.remove(tmp)
    for x in w['words']:
        x['s'], x['e'] = round(x['s'] * pace), round(x['e'] * pace)
    old = w['durationMs']
    w['durationMs'] = round(dur(audio) * 1000)
    w['pace'] = pace
    json.dump(w, open(words, 'w'), indent=1)
    note = ''
    if os.path.exists(raw_clip):
        # Same stretch for the lip-sync clip (muted in the film), so the mouth stays on the slowed voice.
        fps = run('ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries', 'stream=r_frame_rate', '-of', 'csv=p=0', raw_clip).strip()
        run('ffmpeg', '-v', 'error', '-y', '-i', raw_clip, '-vf', f'setpts={pace}*PTS', '-r', fps, '-an', '-c:v', 'libx264', '-crf', '16', '-pix_fmt', 'yuv420p', clip)
        note = f', clip {dur(raw_clip):.2f}s -> {dur(clip):.2f}s'
    print(f'{vid}: {old / 1000:.2f}s -> {w["durationMs"] / 1000:.2f}s{note}', flush=True)
