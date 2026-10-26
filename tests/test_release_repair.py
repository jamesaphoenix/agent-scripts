import importlib.util
from pathlib import Path
import unittest
import tempfile
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('repair',Path(__file__).resolve().parents[1]/'media-quality/video-review/release_repair.py')
r=importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


class RepairTests(unittest.TestCase):
    def test_read_only_snapshots_are_editable_only_in_the_isolated_clone(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);source=base/'original';source.mkdir()
            (source/'src').mkdir();original=source/'src/component.tsx'
            original.write_text('reviewed original');original.chmod(0o444)
            dependency=base/'shared-dependency';dependency.mkdir();shared=dependency/'immutable.txt'
            shared.write_text('shared');shared.chmod(0o444)
            (source/'node_modules').symlink_to(dependency,target_is_directory=True)
            work=base/'revision';work.mkdir()
            with patch.object(r,'WORK',work):
                copy=r.clone(source,work/'copy')
                (copy/'src/component.tsx').write_text('revision')
            self.assertEqual(original.read_text(),'reviewed original')
            self.assertEqual(original.stat().st_mode&0o777,0o444)
            self.assertEqual(shared.stat().st_mode&0o777,0o444)
            self.assertEqual((copy/'node_modules').resolve(),dependency.resolve())

    def source(self):
        return [{'id':'cta-sc-01','kind':'follow','head':'Follow me for more **11+ tips**','script':'[warmly] Follow me for more 11+ tips.'},
                {'id':'cta-sc-05','kind':'promo','script':'old','url':'old'},
                {'id':'cta-sc-10','kind':'promo','script':'old','url':'old'},
                {'id':'cta-sc-08','kind':'promo','script':'Score calculator','url':'smartcublearning.com/tools/calculator/'}]

    def test_subject_links_do_not_replace_diagnostic_or_tool_destinations(self):
        rows={x['id']:x for x in r.cta_rows(self.source(),'vr')}
        self.assertEqual(rows['cta-sc-05']['url'],'smartcublearning.com/11-plus/verbal-reasoning/')
        self.assertIn('forward slash verbal reasoning',rows['cta-sc-05']['script'])
        self.assertEqual(rows['cta-sc-10']['url'],'smartcublearning.com/free-diagnostic')
        self.assertEqual(rows['cta-sc-08']['url'],'smartcublearning.com/tools/calculator/')
        self.assertFalse(rows['cta-sc-05']['circle'])

    def test_only_non_founder_follow_takes_drop_me(self):
        alice=r.cta_rows(self.source(),'maths')[0]
        james=r.cta_rows(self.source())[0]
        self.assertNotIn('Follow me',alice['script'])
        self.assertNotIn('Follow me',alice['head'])
        self.assertIn('Follow me',james['script'])

    def test_practice_claim_and_calm_cta_are_explicit(self):
        rows=r.cta_rows(self.source(),'english')
        self.assertNotIn('diagnostic',rows[1]['script'].lower())
        self.assertIn('Practise',rows[1]['head'])

    def test_display_tokens_keep_original_cues_and_map_to_complete_spoken_numbers(self):
        text,display,spans=r.spoken_parts('[thoughtful] Try 11+ at 37%. Score 121.',True)
        self.assertTrue(text.startswith('[thoughtful]'))
        self.assertEqual(display,['Try','11+','at','37%.','Score','121.'])
        self.assertEqual(text[slice(*spans[1])],'eleven plus')
        self.assertEqual(text[slice(*spans[-1])],'one hundred and twenty-one.')
        plain,_,_=r.spoken_parts('[warmly] Follow for more.',False)
        self.assertEqual(plain,'Follow for more.')
        said,tokens,bounds=r.spoken_parts('called onomatopoeia, but',False,
            {'onomatopoeia':'/ˌɒnəmætəˈpiːə/'})
        self.assertEqual(tokens,['called','onomatopoeia,','but'])
        self.assertIn('/ˌɒnəmætəˈpiːə/',said[slice(*bounds[1])])


if __name__=='__main__':unittest.main()
