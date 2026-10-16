import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('follow_up_render', Path(__file__).with_name('render.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class FollowUpPreflightTests(unittest.TestCase):
    def test_exact_selection_and_all_source_helpers_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            project, app = root / 'project', root / 'app'
            project.mkdir()
            (app / 'scripts').mkdir(parents=True)
            helpers = {}
            for name in ['renderer.ts', 'selection.ts', 'support.ts']:
                path = app / 'scripts' / name
                path.write_text('fixture source')
                helpers['scripts/' + name] = module.sha(path)
            files = []
            for index in range(6):
                path = project / f'source-{index}.txt'
                path.write_text('fixture staged source')
                files.append({'path': path.name, 'afterSha256': module.sha(path)})
            manifest = root / 'manifest.json'
            data = {'project': str(project), 'selection': 'follow-up-pair', 'files': files,
                    'state': 'staged_requires_both_aspect_render_and_review',
                    'originalPublicSourcesModified': False, 'voiceProofs': []}
            manifest.write_text(json.dumps(data))
            with mock.patch.multiple(module, PROJECT=project, APP=app, MANIFEST=manifest,
                                     MANIFEST_SHA=module.sha(manifest), HELPERS=helpers), \
                 mock.patch.object(module.socket, 'gethostname', return_value='JamessMacStudio.fritz.box'):
                self.assertEqual(module.preflight()['stagedFilesVerified'], 6)
                (app / 'scripts/selection.ts').write_text('wrong selection helper')
                with self.assertRaisesRegex(AssertionError, 'Render helper changed'):
                    module.preflight()
                (app / 'scripts/selection.ts').write_text('fixture source')
                data['selection'] = 'different-pair'
                manifest.write_text(json.dumps(data))
                with mock.patch.object(module, 'MANIFEST_SHA', module.sha(manifest)):
                    with self.assertRaises(AssertionError):
                        module.preflight()


if __name__ == '__main__':
    unittest.main()
