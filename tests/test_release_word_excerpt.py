import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/'media-quality/video-review/listen_exact_ending.py'
spec=importlib.util.spec_from_file_location('exact_listening',p)
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


class WordExcerptTests(unittest.TestCase):
    def test_aligned_target_includes_context_and_complete_final_vowel(self):
        start,duration=r.word_excerpt([{'t':'onomatopoeia,','s':48297,'e':49064}],147.4)
        self.assertAlmostEqual(start,44.297)
        self.assertAlmostEqual(duration,8.767)

    def test_missing_or_repeated_target_requires_inspection(self):
        for words in [[],[{'t':'onomatopoeia','s':1000,'e':2000}]*2]:
            with self.assertRaisesRegex(ValueError,'one target'):
                r.word_excerpt(words,100)

    def test_target_cannot_select_cta_audio_or_invalid_alignment(self):
        for start,end,body_end in [(1000,2000,3),(2000,1000,100)]:
            with self.assertRaisesRegex(ValueError,'outside'):
                r.word_excerpt([{'t':'onomatopoeia','s':start,'e':end}],body_end)


if __name__=='__main__':unittest.main()
