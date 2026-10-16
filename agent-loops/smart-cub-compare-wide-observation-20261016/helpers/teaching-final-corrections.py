#!/usr/bin/env python3
"""Five explicitly reviewed copy deltas. Pure planning/patch preparation, no providers or media."""
import hashlib
import json

# Fixed IDs and exact original hashes; never infer these from a provider allowlist.
FINAL_FIVE = {'english.punctuation.punctuate-list': {'lesson': 'y6-punctuate-list',
                                        'firstWord': 39,
                                        'lastWord': 293,
                                        'sourceDataSha256': '95fedad4e5494f850ab7d117666a35a82c84874168d862fc4ed6fcdd6a5e1e44',
                                        'replacements': [["Here, 'The bag contains' is complete.",
                                                          "Here, 'Three things are packed' works."],
                                                         ["Write a comma after 'compass' and after 'banana'.",
                                                          "Write a comma after 'compass', but not 'banana'."],
                                                         ['For the science experiment we need',
                                                          'Our science experiment needs four things'],
                                                         ["after 'need'.", "after 'things'."]]},
 'english.grammar.distinguish-phrase-clause': {'lesson': 'y6-distinguish-phrase-clause',
                                               'firstWord': 28,
                                               'lastWord': 328,
                                               'sourceDataSha256': '1a1e302f20fbbbc7370fe68f4bc409b9a0dcaef3cbaef04eac1c6b66c8dfb940',
                                               'replacements': [['A phrase is a group of words without its '
                                                                 'own subject-and-verb team.',
                                                                 'These phrases contain no verb, so they '
                                                                 'cannot work as complete clauses.'],
                                                                ['A clause has its own subject and verb.',
                                                                 'These clauses each show a subject and '
                                                                 'verb.'],
                                                                ['Remember: find the true subject and its '
                                                                 'verb. That team makes a clause. Without '
                                                                 'that team, you have a phrase.',
                                                                 'Remember: these clauses show their '
                                                                 'subjects and verbs. These phrases lack '
                                                                 'verbs. That contrast helps you classify '
                                                                 'these groups correctly.']]},
 'maths.fdp.convert-decimal-percentage': {'lesson': 'm-convert-decimal-percentage',
                                          'firstWord': 135,
                                          'lastWord': 336,
                                          'sourceDataSha256': 'a953aee737db4b255deceda5eba18e115ada3ffe4e217089b29ab21fa09a983c',
                                          'replacements': [['The digits shift two place-value columns to the '
                                                            'RIGHT.',
                                                            'The decimal point moves two places to the '
                                                            'RIGHT.'],
                                                           ['shifting two columns left.',
                                                            'moving the point left.'],
                                                           ['Shift the digits two place-value columns right.',
                                                            'Move the decimal point two places right.'],
                                                           ['Shift two place-value columns LEFT:',
                                                            'Move the decimal point LEFT:']]},
 'nvr.transform.reflect-figure': {'lesson': 'n-reflect-figure',
                                  'firstWord': 298,
                                  'lastWord': 325,
                                  'sourceDataSha256': 'd65913ca0ab3df310b484262f5791c06199830e8f6eb9f1af46c9b682fb5a80f',
                                  'replacements': [['and preserve each mark, colour, and direction.',
                                                    'and reflect each mark, keeping its colour.']]},
 'maths.fdp.recognise-equivalent-fractions': {'lesson': 'm-recognise-equivalent-fractions',
                                              'firstWord': 285,
                                              'lastWord': 323,
                                              'sourceDataSha256': 'f9e2698497eb75e9c1be6ccdc3ff13c09c86fbbf5a86bae9eab1fb32f1e69b2e',
                                              'replacements': [["And adding NEVER keeps a fraction's value.",
                                                                'And adding one here changes the value.']]}}

VISUAL_PATCHES = {'src/v3/lessons/y6-punctuate-list/Concept.tsx': {'beforeSha256': '9ba4dd0d1594c798aa6ca5bc7be20de5b14d33e5e012e5c817eaa7125576de45',
                                                  'replacements': [['The bag contains',
                                                                    'Three things are packed',
                                                                    4],
                                                                   ['  const c2 = useL(88, 12);\n', '', 1],
                                                                   ['p: and, solid: c2, ghost',
                                                                    'p: and, ghost',
                                                                    1]]},
 'src/v3/lessons/y6-punctuate-list/Worked.tsx': {'beforeSha256': 'de7a678a4bc6fc8e5830139914fb96dc844ad1e2eaf3b5519eb08a3892010145',
                                                 'replacements': [['For the science experiment: we need',
                                                                   'Our science experiment: needs four '
                                                                   'things',
                                                                   1],
                                                                  ['For the science experiment we need',
                                                                   'Our science experiment needs four things',
                                                                   4],
                                                                  ['experiment: we need',
                                                                   'experiment: needs four things',
                                                                   1],
                                                                  ["short: 'we need:",
                                                                   "short: 'needs four things:",
                                                                   3],
                                                                  ["{ id: 'for', t: 'For' }",
                                                                   "{ id: 'for', t: 'Our' }",
                                                                   1],
                                                                  ["{ id: 'the', t: 'the' }",
                                                                   "{ id: 'the', t: 'science' }",
                                                                   1],
                                                                  ["{ id: 'science', t: 'science' }",
                                                                   "{ id: 'science', t: 'experiment' }",
                                                                   1],
                                                                  ["{ id: 'experiment', t: 'experiment', br: "
                                                                   'tall }',
                                                                   "{ id: 'experiment', t: 'needs', br: tall "
                                                                   '}',
                                                                   1],
                                                                  ["{ id: 'we', t: 'we' }",
                                                                   "{ id: 'we', t: 'four' }",
                                                                   1],
                                                                  ["{ id: 'need', t: 'need' }",
                                                                   "{ id: 'need', t: 'things' }",
                                                                   1]]},
 'src/v3/lessons/n-reflect-figure/Recap.tsx': {'beforeSha256': '0738d1df73665db6a118f2e8566b0d757a48ec42018e4b7e4d1f20c779116813',
                                               'replacements': [['Preserve each mark, colour and direction',
                                                                 'Reflect each mark; keep its colour',
                                                                 1]]},
 'src/v3/lessons/y6-distinguish-phrase-clause/concept.tsx': {'beforeSha256': 'f6382631f3013f8c7444425d85767fbe51d0712d0410078a929c7b3fa8470637',
                                                             'replacements': [['const team = useSpan(37, 39, '
                                                                               '0, 6);',
                                                                               'const team = useSpan(31, 32, '
                                                                               '0, 6);',
                                                                               1],
                                                                              ['a group of words without its '
                                                                               'own <Hi p={team} '
                                                                               'color={PHRASE.soft}>subject-and-verb '
                                                                               'team</Hi>',
                                                                               'These phrases have <Hi '
                                                                               'p={team} '
                                                                               'color={PHRASE.soft}>no '
                                                                               'verb</Hi>',
                                                                               1],
                                                                              ['has its <Hi p={own} '
                                                                               'color={CLAUSE.soft}>own '
                                                                               'subject and verb</Hi>',
                                                                               'These clauses show a <Hi '
                                                                               'p={own} '
                                                                               'color={CLAUSE.soft}>subject '
                                                                               'and verb</Hi>',
                                                                               1]]},
 'src/v3/lessons/y6-distinguish-phrase-clause/example.tsx': {'beforeSha256': 'cff9ee3bd30306c68e9bfa992a235d73f05906fd2405c8c9c549904438d78729',
                                                             'replacements': [['const cl = useQ(317);',
                                                                               'const cl = useQ(311);',
                                                                               1],
                                                                              ['const ph = useQ(322);',
                                                                               'const ph = useQ(317);',
                                                                               1],
                                                                              ['const a1 = useSpan(317, 318, '
                                                                               '0, 8);',
                                                                               'const a1 = useSpan(311, 312, '
                                                                               '0, 8);',
                                                                               1],
                                                                              ['const a2 = useSpan(322, 323, '
                                                                               '0, 8);',
                                                                               'const a2 = useSpan(317, 318, '
                                                                               '0, 8);',
                                                                               1],
                                                                              ['const ticks = [useQ(316), '
                                                                               'useQ(321), useQ(328), '
                                                                               'useQ(335)];',
                                                                               'const ticks = [useQ(316), '
                                                                               'useQ(316), useQ(320), '
                                                                               'useQ(335)];',
                                                                               1],
                                                                              ['<Tag>Remember</Tag>',
                                                                               '<Tag>These examples</Tag>',
                                                                               1],
                                                                              ['title="Find the true subject '
                                                                               '+ its verb"',
                                                                               'title="Check these groups"',
                                                                               1],
                                                                              ['title="Team"',
                                                                               'title="Subject + verb"',
                                                                               1],
                                                                              ['title="No team"',
                                                                               'title="No verb"',
                                                                               1]]}}

def replacement_plan(data, skill):
    if skill not in FINAL_FIVE:
        raise ValueError('Unknown final-five skill')
    spec = FINAL_FIVE[skill]
    first, last = spec['firstWord'], spec['lastWord']
    original = ' '.join(w['t'] for w in data['words'][first:last + 1])
    script = ' '.join(b['narration'] for s in data['script']['sections'] for b in s['beats'])
    replacement = original
    for old, new in spec['replacements']:
        if len(old.split()) != len(new.split()) or not replacement.count(old):
            raise ValueError('Exact spoken replacement or word count changed')
        if replacement.count(old) != script.count(old):
            raise ValueError('Script and chosen section do not cover identical replacements')
        replacement = replacement.replace(old, new)
    if len(replacement.split()) != last - first + 1 or len(replacement) > 2000:
        raise ValueError('Final-five section exceeds the bounded word/character contract')
    return {'firstWord': first, 'lastWord': last, 'replacementSpokenText': replacement,
            'approvedWordChanges': [[i + first, a, b] for i, (a, b) in
                enumerate(zip(original.split(), replacement.split())) if a != b],
            'scriptReplacements': spec['replacements']}


def patched_visual(relative, original):
    spec = VISUAL_PATCHES[relative]
    if hashlib.sha256(original).hexdigest() != spec['beforeSha256']:
        raise ValueError('Final-five visual differs from the reviewed source: ' + relative)
    text = original.decode()
    for old, new, count in spec['replacements']:
        if text.count(old) != count:
            raise ValueError('Unexpected final-five visual replacement count: ' + relative)
        text = text.replace(old, new)
    return text.encode()


def final_entry():
    imports = [f"import lesson{i} from '../lessons/{spec['lesson']}';" for i, spec in enumerate(FINAL_FIVE.values())]
    return ("import React from 'react';\nimport { registerRoot } from 'remotion';\n"
            + '\n'.join(imports) + "\nimport { v3Compositions } from '../define';\n"
            + "registerRoot(() => <>{v3Compositions([" + ', '.join(f'lesson{i}' for i in range(5)) + "])}</>);\n").encode()


def final_audio_declaration(text):
    variants = {
        '39f68332b9ce921ff08096a91778ba9cf9095970ffdaf43841e37148fd5afe52': 'audio/${l.data.skill}.mp3',
        '1be39d1b96dfe0d70becb7458ecccaf2fe2033f5a3b6659982d04a68b2c98ab8': 'audio/${l.data.skill}.wav',
    }
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    if original_hash not in variants:
        raise ValueError('Final-five project differs from either reviewed audio definition')
    old = variants[original_hash]
    if text.count(old) != 1:
        raise ValueError('Unexpected pinned audio declaration')
    selected = json.dumps(list(FINAL_FIVE), separators=(',', ':'))
    return text.replace(old, 'audio/${l.data.skill}${' + selected + ".includes(l.data.skill) ? '.wav' : '.mp3'}")


def write_plans(destination):
    """Freeze plans from untouched original inputs on Studio, without processing media."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('final_repair', Path(__file__).with_name('teaching-narration-repair.py'))
    repair = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(repair)
    repair.ledger.require_studio()
    project = Path('/Users/jamesphoenix/render-tmp/smart-cub-refresh-20261016/teaching-repairs/final-style-lab')
    prepared = []
    for skill, expected in FINAL_FIVE.items():
        data_path = project / 'data' / (skill + '.json')
        audio_path = project / 'public/audio' / (skill + '.mp3')
        data_hash = repair.ledger.sha256(data_path)
        if data_hash != expected['sourceDataSha256']:
            raise ValueError('Final-five data differs from original: ' + skill)
        fragment = replacement_plan(repair.ledger.read(data_path), skill)
        plan = {'id': skill, 'sourceData': str(data_path), 'sourceDataSha256': data_hash,
                'sourceAudio': str(audio_path), 'sourceAudioSha256': repair.ledger.sha256(audio_path),
                'voice': repair.VOICE, 'model': repair.MODEL, 'voiceSettings': repair.SETTINGS,
                **fragment, 'reviewer': 'Codex primary agent',
                'scope': 'Corrected social source only; preserve original public inputs and original source media.',
                'status': 'approved_narrow_narration_delta_requires_rendered_media_review'}
        path = destination / (skill + '.json')
        if path.exists() and repair.ledger.read(path) != plan:
            raise ValueError('Existing immutable plan differs: ' + str(path))
        prepared.append((path, plan))
    destination.mkdir(parents=True, exist_ok=True)
    for path, plan in prepared:
        if not path.exists():
            repair.atomic(path, plan)
    return [{'id': plan['id'], 'path': str(path), 'sha256': repair.ledger.sha256(path),
             'characters': len(plan['replacementSpokenText']), 'changes': len(plan['approvedWordChanges'])}
            for path, plan in prepared]


if __name__ == '__main__':
    import argparse
    from pathlib import Path
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--write-plans', type=Path)
    parser.add_argument('--approved-copy', action='store_true', help='Root has accepted these exact spoken deltas')
    args = parser.parse_args()
    if args.write_plans:
        if not args.approved_copy:
            parser.error('--write-plans requires explicit --approved-copy')
        print(json.dumps({'state': 'immutable_plans_written_no_provider_calls', 'plans': write_plans(args.write_plans)}))
    else:
        print(json.dumps({'state': 'plan_only', 'skills': FINAL_FIVE, 'visualFiles': list(VISUAL_PATCHES)}))
