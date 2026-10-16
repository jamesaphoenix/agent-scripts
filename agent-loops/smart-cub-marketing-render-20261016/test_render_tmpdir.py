import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('render_tmpdir', Path(__file__).with_name('render_tmpdir.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class TemporaryDirectoryTests(unittest.TestCase):
    def test_all_runtimes_agree_and_both_lease_locations_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            canonical, legacy = root / 'darwin', root / 'legacy'
            canonical.mkdir()
            legacy.mkdir()
            with mock.patch.dict(os.environ, {'TMPDIR': str(canonical)}):
                self.assertFalse(module.verify(canonical, canonical, canonical, legacy)['leasesModified'])
                for parent in (legacy, canonical):
                    lease = parent / 'smart-cub-founder-render.lock'
                    lease.mkdir()
                    with self.assertRaisesRegex(ValueError, 'explicit ownership reconciliation'):
                        module.verify(canonical, canonical, canonical, legacy)
                    self.assertTrue(lease.is_dir())
                    lease.rmdir()
                for node, python in [(legacy, canonical), (canonical, legacy)]:
                    with self.assertRaisesRegex(ValueError, 'temporary directories differ'):
                        module.verify(canonical, node, python, legacy)
            with mock.patch.dict(os.environ, {'TMPDIR': str(legacy)}):
                with self.assertRaisesRegex(ValueError, 'TMPDIR must be'):
                    module.verify(canonical, canonical, canonical, legacy)


if __name__ == '__main__':
    unittest.main()
