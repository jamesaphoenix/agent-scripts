#!/usr/bin/env python3
"""Prepare the two explicitly requested lesson-body repairs in an isolated source."""
import importlib.util
from pathlib import Path
import shutil
import sys

spec=importlib.util.spec_from_file_location('repair',Path(__file__).with_name('release_repair.py'))
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)
m=r.helpers();m.ledger.require_studio()
original=r.OLD.parent/'audio-layout-revision-20261019/teaching-answer-card-static-batch-source'
root=r.clone(original,r.WORK/'source/teaching-body-repairs')
skill='english.language.identify-sound-device'
source=r.read(original/'data'/f'{skill}.json')
script=' '.join(w['t'] for w in source['words'])
if '--ipa-pilot' in sys.argv or '--cmu-pilot' in sys.argv:
    job={'identity':'teaching-onomatopoeia-ipa-pilot-v2','id':skill,
        'script':' '.join(w['t'] for w in source['words'][113:129]),'root':None,
        'voice':r.ALICE,'model':'eleven_v4','registry':'lesson-pronunciation-pilot','mode':'teaching',
        'pronunciationAliases':{'onomatopoeia':'/ˌɒnəmætəˈpiːə/'},
        'voiceSettings':{'stability':.5,'similarity_boost':.8,'speed':.87035,'use_speaker_boost':True}}
    if '--cmu-pilot' in sys.argv:
        job.update(identity='teaching-onomatopoeia-cmu-pilot-v3',model='eleven_flash_v2',
            pronunciationAliases={'onomatopoeia':'<phoneme alphabet="cmu-arpabet" ph="AA2 N AH0 M AE2 T AH0 P IY1 AH0">onomatopoeia</phoneme>'})
    r.voices(m,[job],0)
    print('IPA-guided Alice pronunciation pilot generated; listening remains required.',flush=True)
    raise SystemExit(0)
identity='teaching-sound-device-cmu-full-v4'
job={'identity':identity,'id':skill,'script':script,'root':None,'voice':r.ALICE,'model':'eleven_flash_v2',
     'registry':'lesson-body','mode':'teaching','pronunciationAliases':{'onomatopoeia':'<phoneme alphabet="cmu-arpabet" ph="AA2 N AH0 M AE2 T AH0 P IY1 AH0">onomatopoeia</phoneme>'},
     'voiceSettings':{'stability':.5,'similarity_boost':.8,'speed':.87035,'use_speaker_boost':True}}
if not (r.WORK/'accepted-pronunciation.json').exists():raise ValueError('Pronunciation pilot needs agent acceptance before complete body generation')
m.atomic_json(r.WORK/'teaching-body-voice-plan.json',job)
r.voices(m,[job],0)
voice=r.WORK/'voices'/identity
words=r.read(voice/'words.json')
if [w['t'] for w in words['words']]!=[w['t'] for w in source['words']]:
    raise ValueError('Replacement narration changed teaching words or indices')
changed={**source,'words':words['words'],'durationMs':words['durationMs']}
m.atomic_json(root/'data'/f'{skill}.json',changed)
shutil.copyfile(voice/'audio.mp3',root/'public/audio'/f'{skill}.mp3')
hook=root/'src/v3/lessons/y6-mark-possession/Hook.tsx'
text=hook.read_text()
lines=[x for x in text.splitlines() if "pick(520, 460), APOS" in x]
if lines:text=text.replace(lines[0],'            {/* Oversized opening apostrophe removed: it overlapped the teaching text. */}')
elif 'Oversized opening apostrophe removed' not in text:raise ValueError('Opening mark source shape changed')
hook.write_text(text)
entry=root/'src/v3/entries/feedback26-body-repairs.tsx'
entry.write_text("import React from 'react';\nimport {registerRoot} from 'remotion';\nimport sound from '../lessons/y6-identify-sound-device';\nimport possession from '../lessons/y6-mark-possession';\nimport {v3Compositions} from '../define';\nregisterRoot(()=><>{v3Compositions([sound,possession])}</>);\n")
m.atomic_json(r.WORK/'teaching-body-stage.json',{'state':'staged_cmu_guided_pronunciation_and_overlap_repair_pending_render',
    'pronunciationListeningAccepted':True,'pronunciationAcceptance':str(r.WORK/'accepted-pronunciation.json'),
    'audioModel':'eleven_flash_v2','fullFinalAudioReview':'pending',
    'source':str(root),'entry':str(entry),'voice':r.ALICE,'originalWordsRetained':len(source['words']),
    'pronunciationWordIndex':118,'newOnomatopoeiaWord':words['words'][118],
    'voiceSettings':job['voiceSettings'],'sourceAudioSha256':r.sha(voice/'audio.mp3'),
    'purpleMarkRemoved':True,'newBodyDurationMs':words['durationMs'],'publicationApproved':False})
print('Staged two teaching-body repairs, with exact display-word indices preserved.',flush=True)

import subprocess
subprocess.run(['/opt/homebrew/bin/node',str(Path(__file__).with_name('render_teaching_bodies.mjs'))],check=True)
