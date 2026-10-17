#!/usr/bin/env python3
"""Install explicitly selected corrections into their isolated Studio copy."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

SPEC = importlib.util.spec_from_file_location('stage_repair', Path(__file__).with_name('teaching-narration-repair.py'))
repair = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repair)
FINAL_SPEC = importlib.util.spec_from_file_location('final_copy', Path(__file__).with_name('teaching-final-corrections.py'))
final_copy = importlib.util.module_from_spec(FINAL_SPEC)
FINAL_SPEC.loader.exec_module(final_copy)
BASE = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/teaching-repairs')


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


SELECTIONS = {
    'original-pair': frozenset({'english.grammar.complete-finite-clause', 'english.grammar.distinguish-contraction-possession'}),
    'pronoun': frozenset({'english.grammar.select-pronoun-form'}),
    'follow-up-pair': frozenset({'english.grammar.select-pronoun-form', 'english.language.expand-common-abbreviation'}),
    'final-five': frozenset({'english.punctuation.punctuate-list', 'english.grammar.distinguish-phrase-clause',
        'maths.fdp.convert-decimal-percentage', 'nvr.transform.reflect-figure', 'maths.fdp.recognise-equivalent-fractions'}),
}


def staging_selection(name):
    if name not in SELECTIONS:
        raise ValueError('Unknown fixed staging selection')
    project = 'style-lab' if name == 'original-pair' else 'final-style-lab' if name == 'final-five' else 'pronoun-style-lab'
    proof = {'original-pair': 'source-patches.json', 'pronoun': 'pronoun-source-patches.json',
             'follow-up-pair': 'follow-up-source-patches.json', 'final-five': 'final-source-patches.json'}[name]
    return SELECTIONS[name], BASE / project, BASE / proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--selection', choices=tuple(SELECTIONS), default='original-pair')
    args = parser.parse_args()
    pronoun = args.selection in ('pronoun', 'follow-up-pair')
    selected, project, proof_path = staging_selection(args.selection)
    if not args.execute:
        print(json.dumps({'state': 'plan_only', 'project': str(project), 'skills': sorted(selected)}))
        return
    repair.ledger.require_studio()
    if proof_path.exists():
        proof = repair.ledger.read(proof_path)
        if proof.get('selection', 'original-pair') != args.selection:
            raise ValueError('Existing staging proof has a different explicit selection')
        for row in proof['files']:
            if checksum(project / row['path']) != row['afterSha256']:
                raise ValueError('Already staged correction source changed; inspect the existing proof')
        print(json.dumps({'state': 'existing_staging_verified', 'proof': str(proof_path)}))
        return
    before = BASE / ('final-source-code-before' if args.selection == 'final-five' else 'pronoun-source-code-before' if pronoun else 'source-code-before')
    changed = []
    def install(relative, content, expected=None):
        target = project / relative
        original = target.read_bytes() if target.exists() else None
        if expected and (original is None or hashlib.sha256(original).hexdigest() != expected):
            raise ValueError('Isolated source differs from the reviewed original: ' + relative)
        if original is not None:
            saved = before / relative;saved.parent.mkdir(parents=True, exist_ok=True)
            if saved.exists() and saved.read_bytes() != original:
                raise ValueError('Preserved source backup differs')
            saved.write_bytes(original)
        target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(content)
        changed.append({'path': relative, 'beforeSha256': hashlib.sha256(original).hexdigest() if original is not None else None,
                        'afterSha256': checksum(target)})
    # Validate complete fixed voice coverage and every final visual before changing any isolated source.
    admitted = [(path, repair.ledger.read(path)) for path in sorted((BASE / 'plans').glob('*.json'))]
    admitted = [(path, plan) for path, plan in admitted if plan.get('id') in selected]
    expected_voice = selected - {'english.language.expand-common-abbreviation'}
    if {plan['id'] for _, plan in admitted} != expected_voice or len(admitted) != len(expected_voice):
        raise ValueError('Fixed selection requires exactly one saved voice plan per narrated target')
    visual = {relative: final_copy.patched_visual(relative, (project / relative).read_bytes())
              for relative in final_copy.VISUAL_PATCHES} if args.selection == 'final-five' else {}
    audio_define = final_copy.final_audio_declaration((project / 'src/v3/define.tsx').read_text()) if args.selection == 'final-five' else None
    validated_jobs = []
    for plan_file, plan in admitted:
        if args.selection == 'final-five':
            expected = final_copy.FINAL_FIVE[plan['id']]
            original_data = project / 'data' / (plan['id'] + '.json')
            if checksum(original_data) != expected['sourceDataSha256'] or plan['sourceDataSha256'] != expected['sourceDataSha256']:
                raise ValueError('Final-five plan must bind the exact original source')
            fragment = final_copy.replacement_plan(repair.ledger.read(original_data), plan['id'])
            if any(plan.get(key) != value for key, value in fragment.items()):
                raise ValueError('Final-five plan differs from the exact accepted copy')
        job = repair.job_directory(plan_file, plan)
        proof = repair.ledger.read(job / 'repair-proof.json')
        if proof['planSha256'] != checksum(plan_file) or proof['repairedDataSha256'] != checksum(job / 'repaired-data.json') \
                or proof['repairedAudioSha256'] != checksum(job / 'repaired.wav'):
            raise ValueError('Paid response or local repair provenance changed')
        validated_jobs.append((plan_file, plan, job))
    # Do not install a partial voice set while another target is missing or invalid.
    for plan_file, plan, job in validated_jobs:
        install('data/' + plan['id'] + '.json', (job / 'repaired-data.json').read_bytes(), plan['sourceDataSha256'])
        install('public/audio/' + plan['id'] + '.wav', (job / 'repaired.wav').read_bytes())
    if args.selection == 'final-five':
        for relative, content in visual.items():
            install(relative, content, final_copy.VISUAL_PATCHES[relative]['beforeSha256'])
        install('src/v3/define.tsx', audio_define.encode())
        install('src/v3/entries/reviewed-social-corrections.tsx', final_copy.final_entry())
        voice_proofs = [repair.job_directory(path, plan) / 'repair-proof.json' for path, plan in admitted]
        proof = {'state': 'staged_requires_both_aspect_render_and_review', 'project': str(project),
                 'selection': args.selection, 'files': changed, 'skills': sorted(selected),
                 'originalPublicSourcesModified': False,
                 'copyImplementation': {'path': str(Path(final_copy.__file__)), 'sha256': checksum(Path(final_copy.__file__))},
                 'voiceProofs': [{'path': str(path), 'sha256': checksum(path)} for path in voice_proofs]}
        repair.atomic(proof_path, proof)
        print(json.dumps({'state': proof['state'], 'modifiedFiles': len(changed), 'proof': str(proof_path)}))
        return
    if pronoun:
        relative = 'src/v3/lessons/y6-select-pronoun-form/Subject.tsx'
        text = (project / relative).read_text()
        replacements = [
            ('a subject does the action and comes before the verb.', 'the shown subjects act before their verbs in these active statements.'),
            ('Job 1 · Subject', 'In these statements'),
            ('A <span style={{ color: SUBJ }}>subject</span> does the action.', 'These <span style={{ color: SUBJ }}>subjects</span> do the action.'),
            ('It comes <span style={{ color: SUBJ }}>before the verb</span>.', 'They come <span style={{ color: SUBJ }}>before the verb</span>.'),
        ]
        for old, new in replacements:
            if text.count(old) != 1:
                raise ValueError('Unexpected original pronoun source: ' + old)
            text = text.replace(old, new)
        install(relative, text.encode())
        entry = '''import React from 'react';
import { registerRoot } from 'remotion';
import pronoun from '../lessons/y6-select-pronoun-form';
import { v3Compositions } from '../define';
registerRoot(() => <>{v3Compositions([pronoun])}</>);
'''
        if args.selection == 'follow-up-pair':
            relative = 'src/v3/lessons/y6-expand-common-abbreviation/Example.tsx'
            text = (project / relative).read_text()
            old = '<span style={{ color: SAINT }}>Church</span> = a saint’s name'
            if text.count(old) != 1:
                raise ValueError('Unexpected original Church context label')
            install(relative, text.replace(old, '<span style={{ color: SAINT }}>Church</span> confirms Saint').encode())
            entry = entry.replace("import pronoun", "import abbreviation from '../lessons/y6-expand-common-abbreviation';\nimport pronoun").replace('[pronoun]', '[pronoun, abbreviation]')
        install('src/v3/entries/reviewed-social-corrections.tsx', entry.encode())
        relative = 'src/v3/define.tsx'
        text = (project / relative).read_text()
        old = 'audio/${l.data.skill}.wav'
        if text.count(old) != 1:
            raise ValueError('Isolated pronoun project must use the reviewed WAV declaration')
        install(relative, text.replace(old, "audio/${l.data.skill}${l.data.skill === 'english.grammar.select-pronoun-form' ? '.wav' : '.mp3'}").encode())
        proof = {'state': 'staged_requires_both_aspect_render_and_review', 'project': str(project),
                 'selection': args.selection, 'files': changed, 'skills': sorted(selected),
                 'originalPublicSourcesModified': False,
                 'voiceProofs': [{'path': str(path), 'sha256': checksum(path)}
                    for path in sorted((BASE / 'plans').glob('english.grammar.select-pronoun-form-*/repair-proof.json'))]}
        repair.atomic(proof_path, proof)
        print(json.dumps({'state': proof['state'], 'modifiedFiles': len(changed), 'proof': str(proof_path)}))
        return
    folder = 'src/v3/lessons/y6-distinguish-contraction-possession/'
    for name, count in [('stem.tsx', 2), ('Paper.tsx', 2), ('Gap1.tsx', 2), ('Answer.tsx', 1)]:
        relative = folder + name;text = (project / relative).read_text()
        result, actual = re.subn(r'\bturned\b', 'turns', text)
        if actual != count:
            raise ValueError('Unexpected robot example source: ' + name)
        install(relative, result.encode())
    relative = 'src/v3/lessons/y6-complete-finite-clause/Concept.tsx'
    text = (project / relative).read_text();old = 'Every complete finite clause needs '
    if text.count(old) != 1:
        raise ValueError('Unexpected finite-clause source')
    install(relative, text.replace(old, 'These complete finite clauses need ').encode())
    relative = 'src/v3/define.tsx';text = (project / relative).read_text()
    if text.count('audio/${l.data.skill}.mp3') != 1:
        raise ValueError('Unexpected source audio declaration')
    install(relative, text.replace('audio/${l.data.skill}.mp3', 'audio/${l.data.skill}.wav').encode())
    install('src/v3/entries/reviewed-social-corrections.tsx', b'''import React from 'react';
import { registerRoot } from 'remotion';
import finite from '../lessons/y6-complete-finite-clause';
import apostrophe from '../lessons/y6-distinguish-contraction-possession';
import { v3Compositions } from '../define';
registerRoot(() => <>{v3Compositions([finite, apostrophe])}</>);
''')
    proof = {'state': 'staged_requires_both_aspect_render_and_review', 'project': str(project), 'files': changed,
             'skills': sorted(selected), 'originalPublicSourcesModified': False,
             'voiceProofs': [{'path': str(path), 'sha256': checksum(path)} for path in sorted((BASE / 'plans').glob('*/repair-proof.json'))
                             if any(path.parent.name.startswith(skill + '-') for skill in selected)]}
    repair.atomic(proof_path, proof)
    print(json.dumps({'state': proof['state'], 'modifiedFiles': len(changed), 'proof': str(proof_path)}))


if __name__ == '__main__':
    main()
