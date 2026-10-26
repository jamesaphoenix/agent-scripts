import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

spec = importlib.util.spec_from_file_location('video_review', Path(__file__).resolve().parents[1] / 'media-quality/video-review/server.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / 'clip.mp4').write_bytes(b'0123456789')
        (root / 'manifest.json').write_text(json.dumps({'videos': [{'kind': 'cta', 'id': 'one', 'aspect': 'tall', 'sha256': 'exact-hash', 'path': 'clip.mp4', 'previous_human_review': {'status': 'fix', 'notes': 'Fade after the last word.'}}]}))
        self.server = module.ReviewServer(('127.0.0.1', 0), root, root / 'state/reviews.json')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f'http://127.0.0.1:{self.server.server_port}'

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_seeking_supports_normal_suffix_and_unsatisfiable_ranges(self):
        for value, expected in [('bytes=2-5', b'2345'), ('bytes=-3', b'789'), ('bytes=8-', b'89')]:
            with urlopen(Request(self.url + '/media/0.mp4', headers={'Range': value})) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), expected)
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + '/media/0.mp4', headers={'Range': 'bytes=99-'}))
        self.assertEqual(error.exception.code, 416)

    def test_review_roundtrip_preserves_notes_and_exact_output_identity(self):
        key = self.server.rows[0]['key']
        data = json.dumps({'key': key, 'status': 'fix', 'notes': '[0:37] Check subtitle'}).encode()
        request = Request(self.url + '/api/reviews', data=data, headers={'Origin': self.url, 'Content-Type': 'application/json'})
        with urlopen(request) as response:
            self.assertEqual(json.load(response)['status'], 'fix')
        with urlopen(self.url + '/api/export') as response:
            saved = json.load(response)
        self.assertEqual(saved['reviews'][key]['notes'], '[0:37] Check subtitle')
        self.assertFalse(saved['automaticPublication'])
        self.assertTrue(self.server.state.with_suffix('.history.jsonl').exists())
        self.assertEqual((self.server.root / 'clip.mp4').read_bytes(), b'0123456789')

    def test_previous_feedback_is_visible_without_becoming_a_new_output_decision(self):
        with urlopen(self.url + '/api/catalog') as response:
            row=json.load(response)['videos'][0]
        self.assertEqual(row['previous_human_review']['notes'],'Fade after the last word.')
        with urlopen(self.url + '/api/reviews') as response:
            self.assertEqual(json.load(response)['reviews'],{})

    def test_cross_origin_writes_and_unlisted_files_are_rejected(self):
        with self.assertRaises(HTTPError) as error:
            urlopen(Request(self.url + '/api/reviews', data=b'{}', headers={'Origin': 'https://example.com'}))
        self.assertEqual(error.exception.code, 403)
        with self.assertRaises(HTTPError) as error:
            urlopen(self.url + '/manifest.json')
        self.assertEqual(error.exception.code, 404)


if __name__ == '__main__':
    unittest.main()
