import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

p=Path(__file__).resolve().parents[1]/'media-quality/video-review/review_release_batch.py'
s=importlib.util.spec_from_file_location('reviews',p);r=importlib.util.module_from_spec(s);s.loader.exec_module(r)


class SafePollTests(unittest.TestCase):
    def test_file_status_503_can_retry_without_another_paid_request(self):
        request=Mock(side_effect=[RuntimeError('Gemini HTTP 503; temporary'),({'state':'ACTIVE'}, {})]);sleep=Mock()
        self.assertEqual(r.request_with_file_poll_retry(request,'/v1beta/files/abc',sleep=sleep),({'state':'ACTIVE'}, {}))
        self.assertEqual(request.call_count,2);sleep.assert_called_once_with(2)

    def test_generation_upload_and_deletion_never_retry(self):
        for endpoint,body,kwargs in [('/v1beta/models/flash:generateContent',{},{}),('/upload/v1beta/files',{},{}),('/v1beta/files/abc',None,{'method':'DELETE'})]:
            request=Mock(side_effect=RuntimeError('Gemini HTTP 503; temporary'))
            with self.assertRaises(RuntimeError):r.request_with_file_poll_retry(request,endpoint,body,sleep=Mock(),**kwargs)
            self.assertEqual(request.call_count,1)

    def test_auth_error_is_not_retried(self):
        request=Mock(side_effect=RuntimeError('Gemini HTTP 403; denied'))
        with self.assertRaises(RuntimeError):r.request_with_file_poll_retry(request,'/v1beta/files/abc',sleep=Mock())
        self.assertEqual(request.call_count,1)

    def test_transient_retries_are_bounded(self):
        request=Mock(side_effect=RuntimeError('Gemini HTTP 503; temporary'))
        with self.assertRaises(RuntimeError):r.request_with_file_poll_retry(request,'/v1beta/files/abc',sleep=Mock())
        self.assertEqual(request.call_count,3)


if __name__=='__main__':unittest.main()
