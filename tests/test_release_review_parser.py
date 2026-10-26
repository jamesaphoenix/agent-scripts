import importlib.util
from pathlib import Path
import unittest
import json
import tempfile

spec=importlib.util.spec_from_file_location('release_review',Path(__file__).resolve().parents[1]/'media-quality/video-review/review_release_batch.py')
r=importlib.util.module_from_spec(spec);spec.loader.exec_module(r)


class ParserTests(unittest.TestCase):
    def test_changed_export_does_not_inherit_a_completed_advisory(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);response=folder/'batch-001-response.json'
            response.write_text('{"saved":"provider response"}')
            item={'identity':'cta|one|tall','id':'one','kind':'cta','aspect':'tall',
                  'output':'/selected/one.mp4','sha256':'old-output'}
            job={'state':'completed_advisory','responsePath':str(response),
                 'responseSha256':r.r.sha(response),'items':[item],
                 'reviews':[{'verdict':'no_additional_defects_observed'}]}
            (folder/'batch-001.json').write_text(json.dumps(job))
            self.assertEqual(len(r.collect_current_reviews(folder,{'cta|one|tall':{'outputSha256':'old-output'}})),1)
            self.assertEqual(r.collect_current_reviews(folder,{'cta|one|tall':{'outputSha256':'corrected-output'}}),{})
            self.assertTrue(response.exists())

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
