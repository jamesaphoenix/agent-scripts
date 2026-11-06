import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT=Path(__file__).resolve().parents[1]/'agent-loops/docker-cleanup/record-hetzner-release.py'
spec=importlib.util.spec_from_file_location('receipt',SCRIPT)
receipt=importlib.util.module_from_spec(spec);spec.loader.exec_module(receipt)

class ReceiptTests(unittest.TestCase):
    def record(self,n):
        return {'artifact':'/opt/octospark/deploy/artifacts/images-'+str(n)+'.env',
                'artifactSha256':str(n),'sourceSha':str(n),'images':{'api':str(n)},
                'evidence':'https://github.com/Just-Understanding-Data-Ltd/octospark/actions/runs/'+str(n)}

    def test_records_every_success_between_cron_runs_and_preserves_other_environments(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);history={p:[self.record(1),self.record(2),self.record(3)] for p in receipt.cleanup.PROJECTS}
            path=root/'rollback-ledger.json';path.write_text(json.dumps({'version':1,'projects':history}))
            with patch.object(receipt.cleanup,'validate_record'):
                receipt.append_receipt(root,'octospark-staging',self.record(4))
                receipt.append_receipt(root,'octospark-staging',self.record(5))
            saved=json.loads(path.read_text())['projects']
            self.assertEqual([r['sourceSha'] for r in saved['octospark-staging']], ['1','2','3','4','5'])
            self.assertEqual(saved['octospark-prod'],history['octospark-prod'])
            self.assertEqual(len(list((root/'receipt-archive').glob('*.json'))),2)
            selected=receipt.cleanup.retain_history(saved,{p:saved[p][-1] for p in saved})
            self.assertEqual([r['sourceSha'] for r in selected['octospark-staging']],['3','4','5'])

    def test_repeated_delivery_of_same_release_does_not_duplicate_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); history={p:[self.record(1),self.record(2),self.record(3)] for p in receipt.cleanup.PROJECTS}
            path=root/'rollback-ledger.json';path.write_text(json.dumps({'version':1,'projects':history}))
            with patch.object(receipt.cleanup,'validate_record'):
                receipt.append_receipt(root,'octospark-prod',self.record(3))
            self.assertEqual(json.loads(path.read_text())['projects'],history)

    def test_wrong_product_evidence_and_artifact_paths_are_rejected(self):
        with self.assertRaises(ValueError): receipt.validate_inputs('octospark-prod','/tmp/images.env','https://github.com/Just-Understanding-Data-Ltd/octospark/actions/runs/123')
        with self.assertRaises(ValueError): receipt.validate_inputs('octospark-prod','/opt/octospark/deploy/artifacts/images-a.env','https://github.com/Just-Understanding-Data-Ltd/smart-cub-learning/actions/runs/123')

    def test_incomplete_ledger_is_not_bootstrapped_from_one_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'rollback-ledger.json').write_text('{"version":1,"projects":{}}')
            with self.assertRaises(ValueError): receipt.append_receipt(root,'octospark-prod',self.record(1))


class ReceiptVerificationTests(unittest.TestCase):
    def test_declared_release_must_be_the_current_healthy_bundle(self):
        with patch.object(receipt.cleanup,'current_release',return_value={'images':{'API_IMAGE':'old'}}), \
             patch.object(receipt.cleanup,'inspect_all') as inventory:
            with self.assertRaisesRegex(ValueError,'current healthy'):
                receipt.verify_current('trace-learn-staging',{'images':{'API_IMAGE':'new'}},[])
            inventory.assert_not_called()

    def test_current_bundle_with_missing_images_is_refused(self):
        record={'images':{'API_IMAGE':'missing'}}
        with patch.object(receipt.cleanup,'current_release',return_value=record), \
             patch.object(receipt.cleanup,'inspect_all',return_value=[]):
            with self.assertRaisesRegex(ValueError,'not all available'):
                receipt.verify_current('trace-learn-prod',record,[])

    def test_native_proof_must_have_passed_for_this_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'release-prod.json').write_text(json.dumps({'environment':'prod','canaryPassed':False}))
            record={'images':{'RENDERER_IMAGE_AMD64':'renderer','MEDIA_RUNTIME_IMAGE_AMD64':'media'}}
            with patch.object(receipt.cleanup,'NATIVE',root),patch.object(receipt.cleanup,'current_release',return_value=record):
                with self.assertRaisesRegex(ValueError,'Native canary'):
                    receipt.verify_current('octospark-prod',record,[])

    def test_retired_project_requires_explicit_reactivation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);ledger={'version':1,'projects':{p:[] for p in receipt.cleanup.PROJECTS},'retiredProjects':['trace-learn-staging']}
            (root/'rollback-ledger.json').write_text(json.dumps(ledger))
            with self.assertRaisesRegex(ValueError,'explicitly retired'):
                receipt.append_receipt(root,'trace-learn-staging',{})

if __name__ == '__main__': unittest.main()
