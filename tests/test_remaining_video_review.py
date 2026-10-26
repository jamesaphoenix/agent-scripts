import contextlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

spec = importlib.util.spec_from_file_location('remaining', Path(__file__).resolve().parents[1] / 'media-quality/video-review/review_remaining.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)


class RemainingReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.w = Path(self.tmp.name)
        (self.w / '.runtime-secrets').mkdir()
        p = self.w / '.runtime-secrets/gemini-key'
        p.write_text('not-a-real-key')
        p.chmod(0o600)
        self.patch = patch.multiple(review, OUT=self.w, WORK=self.w, AUTH=self.w / 'budget.json')
        self.patch.start()
        self.plan = {'total': 1, 'items': [{'key': 'one', 'path': str(self.w / 'clip.mp4'), 'sha256': 'hash',
                                         'id': 'one', 'aspect': 'tall', 'title': 'One', 'knownGlobalChanges': []}]}
        (self.w / 'clip.mp4').write_bytes(b'example-video')

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def media(self, available):
        @contextlib.contextmanager
        def transaction(_):
            yield {'budget': {}}
        def atomic(path, data):
            path.write_text(json.dumps(data))
        client = SimpleNamespace(model=Mock(return_value={'name': 'models/test-model'}),
            request=Mock(return_value=({'totalTokens': 1}, {})),
            upload=Mock(), delete=Mock())
        class Client:
            def __new__(cls):
                return client
        ledger = SimpleNamespace(now=lambda: 'now', transaction=transaction,
            budget_totals=lambda _: {'available_cents': available}, reserve=Mock(), settle=Mock())
        return SimpleNamespace(Gemini=Client, MODEL='test-model', ledger=ledger,
            tariff_current=lambda: None, atomic_json=atomic,
            preflight=lambda _: {'maximum_cents': 10}), client

    def test_insufficient_budget_stops_before_upload_or_generation(self):
        media, client = self.media(3)
        with patch('builtins.print'):
            review.run(media, self.plan, 0)
        client.upload.assert_not_called()
        client.request.assert_not_called()
        media.ledger.reserve.assert_not_called()
        self.assertEqual(review.read(self.w / 'status.json')['state'], 'stopped_at_existing_budget_limit')

    def test_uncertain_paid_request_is_never_retried(self):
        media, client = self.media(232)
        (self.w / 'batch-001.json').write_text(json.dumps({'state': 'submitted_unknown', 'reservationId': 'held'}))
        with patch('builtins.print'), self.assertRaisesRegex(ValueError, 'never automatically retry'):
            review.run(media, self.plan, 0)
        client.request.assert_not_called()
        client.upload.assert_not_called()
        media.ledger.reserve.assert_not_called()

    def test_preflight_rejection_cleans_provider_upload_without_generation(self):
        media, client = self.media(9)
        def upload(path, remember):
            file = {'name': 'files/test', 'uri': 'https://example.com/video'}
            remember(file)
            return file
        client.upload.side_effect = upload
        with patch('builtins.print'), patch.object(review, 'sha', return_value='hash'), patch.object(
            review.subprocess, 'run', return_value=SimpleNamespace(stdout='{"format":{"duration":"30"}}')):
            review.run(media, self.plan, 0)
        media.ledger.reserve.assert_not_called()
        media.ledger.settle.assert_not_called()
        self.assertEqual(client.request.call_count, 1)
        self.assertTrue(client.request.call_args.args[0].endswith(':countTokens'))
        client.delete.assert_called_once_with('files/test')


if __name__ == '__main__':
    unittest.main()
