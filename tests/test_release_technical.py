import importlib.util
from pathlib import Path
import unittest

p=Path(__file__).resolve().parents[1]/'media-quality/video-review/verify_release.py'
s=importlib.util.spec_from_file_location('technical',p);v=importlib.util.module_from_spec(s);s.loader.exec_module(v)

class AudioBoundaryTests(unittest.TestCase):
    def test_open_silence_at_end_is_closed_at_the_decoded_window_boundary(self):
        log='[silencedetect] silence_start: 2.0539\n'
        self.assertEqual(v.silence_spans(log,4),[(2.0539,4)])

    def test_distinguishes_internal_pause_from_trailing_silence(self):
        log='silence_start: 0.2\nsilence_end: 0.35 | silence_duration: 0.15\nsilence_start: 2.1\nsilence_end: 4 | silence_duration: 1.9\n'
        self.assertEqual(v.silence_spans(log,4),[(.2,.35),(2.1,4)])

if __name__=='__main__':unittest.main()
