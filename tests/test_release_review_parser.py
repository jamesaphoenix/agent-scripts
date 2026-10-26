import importlib.util
from pathlib import Path
import unittest

spec=importlib.util.spec_from_file_location('release_review',Path(__file__).resolve().parents[1]/'media-quality/video-review/review_release_batch.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


class ParserTests(unittest.TestCase):
    def response(self,text,finish='STOP'):
        return {'candidates':[{'finishReason':finish,'content':{'parts':[{'text':text}]}}]}

    def test_saved_response_with_preamble_can_be_recovered_without_another_request(self):
        result=r.answer(self.response('Unexpected model prose. ```json\n{"reviews":[{"slot":0}]}\n```'))
        self.assertEqual(result,{'reviews':[{'slot':0}]})

    def test_truncated_output_never_becomes_a_completed_advisory(self):
        with self.assertRaisesRegex(ValueError,'Incomplete'):
            r.answer(self.response('{"reviews":[]}',finish='MAX_TOKENS'))

    def test_non_json_model_commentary_is_not_a_review(self):
        with self.assertRaisesRegex(ValueError,'No valid review JSON'):
            r.answer(self.response('All videos seem fine.'))


if __name__=='__main__':unittest.main()
