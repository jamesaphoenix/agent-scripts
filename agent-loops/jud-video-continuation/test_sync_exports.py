import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('sync_exports', Path(__file__).with_name('sync_exports.py'))
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class ArchivedExports(unittest.TestCase):
    def test_only_archived_pairs_and_no_changed_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            run = Path(directory)
            (run/'regeneration-manifest-v1.json').write_text(json.dumps({'films':[
                {'filmGroupId':f'film-{i}'} for i in range(51)]}))
            root = run/'episodes/film-0'
            receipt = {}
            for aspect in ['portrait', 'landscape']:
                movie = root/'delivery'/aspect/'final.mp4'
                movie.parent.mkdir(parents=True)
                movie.write_bytes(b'known archived content '+aspect.encode())
                receipt[aspect] = {'sha256':sync.sha(movie), 'watch':'https://example.test/private'}
            (root/'archive-receipt.json').write_text(json.dumps(receipt))
            unarchived = run/'episodes/film-1/delivery/portrait/final.mp4'
            unarchived.parent.mkdir(parents=True)
            unarchived.write_bytes(b'not archived')
            verified = {}
            movies, files = sync.collect(run, verified)
            self.assertEqual(len(movies), 2)
            self.assertNotIn(str(unarchived.relative_to(run)), files)
            (root/'delivery/portrait/final.mp4').write_bytes(b'changed after archive')
            with self.assertRaisesRegex(ValueError, 'Archived movie changed'):
                sync.collect(run, verified)


if __name__ == '__main__':
    unittest.main()
