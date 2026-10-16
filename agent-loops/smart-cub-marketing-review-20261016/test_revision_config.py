import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('revision_config', Path(__file__).with_name('revision_config.py'))
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class RevisionHandoffTests(unittest.TestCase):
    def fixture(self, root):
        app = root / 'revision/app'
        app.mkdir(parents=True)
        names = ['scripts/review-media.py', 'scripts/review-ledger.py', 'data/scripts.json',
                 'data/ctas.json', 'data/cta-assignments.json', 'scripts/render-batch.ts',
                 'scripts/render-batch-support.ts']
        protected = {}
        for name in names:
            path = app / name
            path.parent.mkdir(exist_ok=True)
            path.write_text('test source')
            protected[name] = module.sha(path)
        proof = root / 'contracts.json'
        proof.write_text(json.dumps({'status': 'pass', 'checks': [{'status': 'pass'} for _ in range(149)]}))
        data = {'app': str(app), 'reviewEvidenceRoot': str(app / 'out/review-complete'),
                'renderPhases': ['marketing'],
                'frozenSourceSnapshot': 'a' * 64, 'verifiedNarrationContracts': 149,
                'narrationContractProof': {'path': str(proof), 'sha256': module.sha(proof)},
                'protectedSourceHashes': protected}
        config = root / 'config.json'
        config.write_text(json.dumps(data))
        return config, data, app

    def test_accepts_exact_isolated_handoff_and_refuses_changed_protected_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, data, app = self.fixture(root)
            self.assertEqual(module.load_revision(config, root)[0], data)
            (app / 'data/scripts.json').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'Protected revision input changed'):
                module.load_revision(config, root)

    def test_canonical_teaching_app_cannot_be_revision_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, data, _ = self.fixture(root)
            data['app'] = str(root / 'smart-cub/apps/prototypes/founder-videos')
            config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'canonical teaching graph'):
                module.load_revision(config, root)

    def test_contract_proof_and_count_are_required(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, data, _ = self.fixture(root)
            data['verifiedNarrationContracts'] = 148
            config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'all149'):
                module.load_revision(config, root)
            data['verifiedNarrationContracts'] = 149
            config.write_text(json.dumps(data))
            (root / 'contracts.json').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'Narration-contract proof changed'):
                module.load_revision(config, root)

    def test_review_cache_cannot_use_the_canonical_teaching_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, data, app = self.fixture(root)
            data['reviewEvidenceRoot'] = str(root / 'smart-cub/apps/prototypes/founder-videos/out/review-complete')
            config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'own output directory'):
                module.load_revision(config, root)

    def test_revision_cannot_rerender_the_canonical_cta_family(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config, data, _ = self.fixture(root)
            data['renderPhases'] = ['ctas', 'marketing']
            config.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'marketing only'):
                module.load_revision(config, root)


if __name__ == '__main__':
    unittest.main()
