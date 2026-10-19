"""Run on Studio: python -m unittest discover -s media-quality/text-jitter -v."""
import importlib.util
import pathlib
import tempfile
import unittest

import cv2
import numpy as np

SPEC = importlib.util.spec_from_file_location('text_jitter', pathlib.Path(__file__).with_name('text_jitter.py'))
jitter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jitter)


def image(dx=0, dy=0, rule=False, words='Smart Cub Learning'):
    frame = np.full((240, 640), 245, np.uint8)
    cv2.putText(frame, words, (80 + dx, 110 + dy), cv2.FONT_HERSHEY_SIMPLEX,
                1.0, 25, 2, cv2.LINE_AA)
    if rule:
        cv2.rectangle(frame, (62, 72), (68, 118), 20, -1)
    return frame


class TrackingTests(unittest.TestCase):
    box = [77, 80, 340, 38]

    def track(self, offsets, **kwargs):
        frames = [(i, i / 30, image(dx, dy, rule=bool(i % 2)))
                  for i, (dx, dy) in enumerate(offsets)]
        return jitter.measure(frames, self.box, text='Smart Cub Learning', **kwargs)

    def test_static_text_ignores_flickering_neighbouring_rule(self):
        result = self.track([(0, 0)] * 45)
        self.assertEqual(result['status'], 'clear')
        self.assertLess(result['range_px'], .1)

    def test_one_frame_three_pixel_jump_is_found(self):
        offsets = [(0, 0)] * 45
        offsets[24] = (3, 0)
        result = self.track(offsets)
        self.assertEqual(result['status'], 'finding')
        self.assertGreater(result['max_step_px'], 2.5)

    def test_repeated_jitter(self):
        result = self.track([(3 if i % 4 < 2 else -3, 0) for i in range(45)])
        self.assertEqual(result['status'], 'finding')
        self.assertGreater(result['range_px'], 5)

    def test_slow_drift(self):
        result = self.track([(i // 10, 0) for i in range(60)])
        self.assertEqual(result['status'], 'finding')
        self.assertGreater(result['range_px'], 4)

    def test_blank_is_unscorable_never_clear(self):
        frames = [(i, i / 30, np.full((240, 640), 245, np.uint8)) for i in range(45)]
        self.assertEqual(jitter.measure(frames, self.box)['status'], 'unscorable')

    def test_replaced_text_is_unscorable_never_clear(self):
        frames = [(i, i / 30, image(words='Completely changed' if i < 22 else 'Smart Cub Learning'))
                  for i in range(45)]
        self.assertEqual(jitter.measure(frames, self.box)['status'], 'unscorable')

    def test_out_of_search_range_is_unscorable_never_clear(self):
        frames = [(i, i / 30, image(90 if i < 20 else 0)) for i in range(45)]
        self.assertEqual(jitter.measure(frames, self.box)['status'], 'unscorable')

    def test_pure_translation_subpixel_peak_is_finite(self):
        result = self.track([(0, 0)] * 45)
        self.assertTrue(all(np.isfinite(s['dx']) and np.isfinite(s['dy']) for s in result['samples']))

    def test_exclusions_require_reasons_and_match_time_and_region(self):
        with self.assertRaises(ValueError):
            jitter.validate_exclusions([{'start': 0, 'end': 2}])
        ex = [{'start': 0, 'end': 2, 'box': [0, 0, 100, 100], 'reason': 'Reviewed title entrance'}]
        self.assertTrue(jitter.excluded(ex, .2, 1.8, [20, 20, 50, 50]))
        self.assertFalse(jitter.excluded(ex, .2, 2.1, [20, 20, 50, 50]))
        self.assertFalse(jitter.excluded(ex, .2, 1.8, [200, 20, 50, 50]))

    def test_native_frame_reader_and_encoded_jitter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / 'encoded.mp4'
            frames = [image(3 if i % 4 < 2 else -3) for i in range(60)]
            jitter.encode_fixture(path, frames, 30)
            info = jitter.probe(path)
            rows = list(jitter.frames(path, info, .5, 1.5))
            self.assertEqual(len(rows), 30)
            self.assertEqual(rows[0][0], 15)
            self.assertAlmostEqual(rows[-1][1], 44 / 30)
            self.assertEqual(jitter.measure(rows, self.box)['status'], 'finding')


if __name__ == '__main__':
    unittest.main()
