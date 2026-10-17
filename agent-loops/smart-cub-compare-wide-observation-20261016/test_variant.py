import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
spec = importlib.util.spec_from_file_location('policy', HERE / 'helpers/variant-policy.py')
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


class PolicyTests(unittest.TestCase):
    def test_prompt_variant_has_distinct_identity_and_keeps_uncertainty_instruction(self):
        spec = importlib.util.spec_from_file_location('isolated_media', HERE / 'helpers/review-media.py')
        media = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(media)
        context = {'id': p.IDENTITY, 'kind': 'teaching'}
        original = media.original_make_prompt(context, 'wide', {'duration_seconds': 170.366667})
        revised = media.make_prompt(context, 'wide', {'duration_seconds': 170.366667})
        self.assertTrue(revised.startswith(original))
        self.assertNotEqual(media.text_hash(original), media.text_hash(revised))
        for text in ['01:00', '01:40', '02:20', '02:39.333', 'observed_complete=false', 'not supplied observations']:
            self.assertIn(text, revised)
        with self.assertRaises(ValueError):
            media.make_prompt(context, 'tall', {})

    def test_unresolved_original_cannot_authorise_new_intent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old = root / 'old.json'
            old.write_text(json.dumps({'id': p.IDENTITY, 'aspect': 'wide', 'version': {'output_sha256': p.OUTPUT},
                                      'state': 'completed_advisory', 'advisory': {'observed_complete': False},
                                      'reservation_id': 'old'}))
            ledger = root / 'ledger.json'
            ledger.write_text(json.dumps({'budget': {'reservations': {'old': {'state': 'settled', 'actual_cents': 3}}}}))
            with patch.object(p, 'OLD_REQUEST', old), patch.object(p, 'OLD_SHA', p.sha(old)), patch.object(p, 'LEDGER', ledger):
                p.require_terminal_origin()
                ledger.write_text(json.dumps({'budget': {'reservations': {'old': {'state': 'reserved'}}}}))
                with self.assertRaisesRegex(ValueError, 'unresolved'):
                    p.require_terminal_origin()

    def test_other_aspect_output_and_identity_refused(self):
        p.require_target(p.IDENTITY, 'wide', p.OUTPUT)
        for identity, aspect, output in [(p.IDENTITY, 'tall', p.OUTPUT), ('other', 'wide', p.OUTPUT),
                                         (p.IDENTITY, 'wide', '0' * 64)]:
            with self.assertRaises(ValueError):
                p.require_target(identity, aspect, output)

    def test_cost_cap_and_sole_target_enforced_before_client(self):
        p.require_bound({'maximum_cents': 11})
        for bound in [0, 12, '11', None]:
            with self.assertRaises(ValueError):
                p.require_bound({'maximum_cents': bound})
        args = SimpleNamespace(ids=[p.IDENTITY], aspect='wide', kinds=None, all=False,
                               root=p.ROOT, evidence_root=p.EVIDENCE, ledger=p.LEDGER,
                               max_requests=1, mode='model')
        p.require_args(args)
        for key, value in [('aspect', 'both'), ('max_requests', 2), ('evidence_root', p.ROOT / 'out/review-complete'),
                           ('root', Path('/tmp/foreign')), ('mode', 'cleanup')]:
            changed = copy.copy(args)
            setattr(changed, key, value)
            with self.assertRaises(ValueError):
                p.require_args(changed)

    def test_explicit_new_job_missing_or_old_cache_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            isolated = root / 'isolated'
            isolated.mkdir()
            old = root / 'old.json'
            old.write_text('{}')
            with patch.object(p, 'EVIDENCE', isolated):
                with self.assertRaisesRegex(ValueError, 'explicitly selected'):
                    p.require_explicit_job(root, old, p.sha(old), old, {}, None)
                missing = isolated / 'missing.json'
                with self.assertRaises(FileNotFoundError):
                    p.require_explicit_job(root, missing, '0' * 64, missing, {}, None)

    def test_exact_variant_job_rejects_tamper_old_implementation_and_uncertainty(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename, data in [('context.json', {}), ('technical.json', {}), ('body.json', {})]:
                (root / filename).write_text(json.dumps(data))
            implementation = root / 'review-media.py'
            implementation.write_text('new scoped implementation')
            prompt = 'whole clip checkpoints' + p.APPENDIX
            prompt_path = root / 'prompt.txt'
            prompt_path.write_text(prompt)
            digest = lambda value: hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()
            body = {'contents': [{'parts': [{'fileData': {'uri': 'provider'}, 'videoMetadata': {'fps': 2},
                                             'mediaProcessing': 'STATIC'}, {'text': prompt}]}]}
            (root / 'body.json').write_text(json.dumps(body))
            media = SimpleNamespace(__file__=str(implementation), make_prompt=lambda *args: prompt,
                                    technical_summary=lambda value: value, text_hash=lambda value: hashlib.sha256(value.encode()).hexdigest(),
                                    request_body=lambda *args: body, ledger=SimpleNamespace(digest_json=digest))
            version = {'output_sha256': p.OUTPUT, 'context_sha256': 'context', 'prompt_sha256': p.sha(prompt_path),
                       'request_template_sha256': digest(body), 'implementation_sha256': p.sha(implementation),
                       'model': 'model', 'model_metadata': {}}
            job = {'id': p.IDENTITY, 'aspect': 'wide', 'state': 'completed_advisory', 'budget_state': 'settled_usage',
                   'version': version, 'version_key': digest(version), 'context_path': str(root / 'context.json'),
                   'technical_evidence': str(root / 'technical.json'), 'prompt_path': str(prompt_path),
                   'request_body_path': str(root / 'body.json'), 'preflight': {'maximum_cents': 11}}
            request = root / 'request.json'
            request.write_text(json.dumps(job))
            with patch.object(p, 'EVIDENCE', root):
                p.require_explicit_job(root, request, p.sha(request), request, job, media)
                with self.assertRaisesRegex(ValueError, 'bytes changed'):
                    p.require_explicit_job(root, request, '0' * 64, request, job, media)
                for field, value in [('state', 'submitted_unknown'), ('budget_state', 'reserved')]:
                    changed = copy.deepcopy(job)
                    changed[field] = value
                    with self.assertRaisesRegex(ValueError, 'completed'):
                        p.require_explicit_job(root, request, p.sha(request), request, changed, media)
                changed = copy.deepcopy(job)
                changed['version']['implementation_sha256'] = 'old implementation'
                with self.assertRaisesRegex(ValueError, 'implementation'):
                    p.require_explicit_job(root, request, p.sha(request), request, changed, media)
                changed = copy.deepcopy(job)
                changed['version']['prompt_sha256'] = 'old prompt'
                with self.assertRaisesRegex(ValueError, 'prompt'):
                    p.require_explicit_job(root, request, p.sha(request), request, changed, media)


if __name__ == '__main__':
    unittest.main()
