import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]/'agent-loops/docker-cleanup'
def module(name,file):
    spec=importlib.util.spec_from_file_location(name,ROOT/file)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result);return result
cleanup=module('native_cleanup','cleanup-hetzner-images.py')
receipt=module('native_receipt','record-hetzner-release.py')
EVIDENCE='https://github.com/Just-Understanding-Data-Ltd/octospark/actions/runs/123'

class NativeRetentionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.native=self.root/'native';self.native.mkdir()
        self.state=self.root/'state';self.state.mkdir()
        self.addCleanup(patch.stopall)
        patch.object(cleanup,'NATIVE',self.native).start()
        patch.object(receipt.cleanup,'NATIVE',self.native).start()

    def artifact(self,n,environment,renderer=None):
        directory=self.native/'releases'/f'{n}-{environment}';directory.mkdir(parents=True,exist_ok=True)
        fields={'SOURCE_SHA':str(n)*40,'COMPUTE_SOURCE_SHA':str(n)*40}
        for key,repo in cleanup.KEYS.items():
            pin=renderer if key=='RENDERER_IMAGE_AMD64' and renderer else n
            fields[key]=cleanup.PREFIX['octospark']+repo+'@sha256:'+str(pin)*64
        (directory/'images.env').write_text(''.join(k+'='+v+'\n' for k,v in fields.items()))
        return directory

    def proof(self,n,environment,render=None):
        media=self.artifact(n,environment);renderer=render or media
        m=cleanup.read_artifact(media/'images.env','octospark')['images']
        r=cleanup.read_artifact(renderer/'images.env','octospark')['images']
        data={'environment':environment,'canaryPassed':True,'checkedAt':123,'sourceSha':str(n)*40,
              'release':str(media),'renderRelease':str(renderer),'renderSourceSha':renderer.name[0]*40,
              'mediaImage':m['MEDIA_RUNTIME_IMAGE_AMD64'],'rendererImage':r['RENDERER_IMAGE_AMD64']}
        env=self.native/'env';env.mkdir(exist_ok=True)
        values={'BUILD_COMMIT_SHA':str(n)*40,'MEDIA_RUNTIME_IMAGE_DIGEST':data['mediaImage']}
        payload=''.join(k+'='+values[k]+'\n' for k in sorted(values))
        (env/f'media-{environment}.env').write_text(payload)
        (env/'render.env').write_text('RENDER_HOST_RENDERER_IMAGE='+data['rendererImage']+'\n')
        data['envHash']=hashlib.sha256(payload.encode()).hexdigest()
        (self.native/f'release-{environment}.json').write_text(json.dumps(data))
        link=self.native/f'current-media-{environment}';link.unlink(missing_ok=True);link.symlink_to(media)
        current=self.native/'current-render';current.unlink(missing_ok=True);current.symlink_to(renderer)
        return cleanup.current_native_record(environment,EVIDENCE)

    def audited(self,n,environment,renderer=None):
        directory=self.artifact(n,environment,renderer)
        return cleanup.audited_native_record(environment,directory/'images.env',EVIDENCE)

    def histories(self):
        return {env:[self.audited(n,env) for n in (1,2,3)] for env in ('staging','prod')}

    def test_retired_application_can_advance_native_history_without_changing_app_history(self):
        histories=self.histories();current={env:self.proof(3,env) for env in histories}
        apps={p:[{'unchanged':p}] for p in cleanup.PROJECTS}
        ledger={'version':1,'projects':apps,'nativeProjects':histories,'retiredProjects':['octospark-staging']}
        path=self.state/'rollback-ledger.json';path.write_text(json.dumps(ledger))
        current['staging']=self.proof(4,'staging')
        with self.assertRaisesRegex(ValueError,'native success history'):
            cleanup.retain_native_history(histories,current)
        with patch.object(receipt.cleanup,'validate_record'):
            receipt.append_receipt(self.state,'octospark-staging',None,current['staging'])
        saved=json.loads(path.read_text())
        self.assertEqual(saved['projects'],apps)
        self.assertEqual(saved['retiredProjects'],ledger['retiredProjects'])
        selected=cleanup.retain_native_history(saved['nativeProjects'],current)
        self.assertEqual([r['media']['sourceSha'][0] for r in selected['staging']],['2','3','4'])
        self.assertEqual(selected['prod'],histories['prod'])

    def test_distinct_native_binaries_count_even_when_docker_images_are_reused(self):
        rows=[self.audited(n,'staging') for n in (1,2,3)]
        # Native source identity remains distinct when image versions are reused.
        for row in rows:
            for role in ('media','render'):
                row[role]['images']=copy.deepcopy(rows[0][role]['images'])
        selected=cleanup.retain_native_history({'staging':rows,'prod':rows},{'staging':rows[-1],'prod':rows[-1]})
        self.assertEqual(len(selected['staging']),3)

    def test_unused_selected_images_do_not_create_a_new_native_rollback_identity(self):
        row=self.audited(1,'staging')
        other=copy.deepcopy(row)
        other['render']=copy.deepcopy(other['render'])
        other['media']['images']['RENDERER_IMAGE_AMD64']='unused-new-renderer'
        other['render']['images']['MEDIA_RUNTIME_IMAGE_AMD64']='unused-new-media'
        self.assertEqual(cleanup.native_identity(row),cleanup.native_identity(other))

    def test_repeated_bundle_cannot_replace_two_real_rollbacks(self):
        row=self.audited(1,'staging')
        with self.assertRaisesRegex(ValueError,'two distinct native rollback'):
            cleanup.retain_native_history({'staging':[row]*3,'prod':[row]*3},{'staging':row,'prod':row})

    def test_missing_native_history_never_promotes_current_implicitly(self):
        with self.assertRaisesRegex(ValueError,'both native environments'):
            cleanup.retain_native_history({}, {})

    def test_separate_shared_renderer_artifact_is_retained(self):
        renderer=self.artifact(2,'prod');row=self.proof(3,'staging',renderer)
        refs=cleanup.native_record_refs(row)
        self.assertEqual(len(refs),2)
        self.assertIn(cleanup.PREFIX['octospark']+'renderer@sha256:'+'2'*64,refs)
        self.assertIn(cleanup.PREFIX['octospark']+'media-runtime@sha256:'+'3'*64,refs)
        self.assertNotIn(cleanup.PREFIX['octospark']+'renderer@sha256:'+'3'*64,refs)
        self.assertNotIn(cleanup.PREFIX['octospark']+'media-runtime@sha256:'+'2'*64,refs)
        # A subsequent production renderer switch does not falsify staging's
        # immutable completed canary receipt.
        self.proof(4,'prod')
        self.assertEqual(cleanup.current_native_record('staging',EVIDENCE),row)

    def test_snapshot_pins_only_the_actual_shared_renderer_not_an_unused_selected_image(self):
        history={'staging':[self.audited(1,'staging'),self.audited(2,'staging')],
                 'prod':[self.audited(n,'prod') for n in (5,1,2)]}
        history['staging'].append(self.proof(3,'staging',self.artifact(2,'prod')))
        self.proof(2,'prod')
        refs,_,_=cleanup.native_pins(history)
        self.assertIn(cleanup.PREFIX['octospark']+'renderer@sha256:'+'2'*64,refs)
        self.assertIn(cleanup.PREFIX['octospark']+'media-runtime@sha256:'+'3'*64,refs)
        self.assertNotIn(cleanup.PREFIX['octospark']+'renderer@sha256:'+'3'*64,refs)

    def test_wrong_media_symlink_or_changed_artifact_refuses_current_proof(self):
        self.proof(3,'staging')
        link=self.native/'current-media-staging';link.unlink();link.symlink_to(self.artifact(4,'staging'))
        with self.assertRaisesRegex(ValueError,'media symlink'):
            cleanup.current_native_record('staging',EVIDENCE)

    def test_failed_canary_or_source_mismatch_refuses_current_proof(self):
        self.proof(3,'staging');path=self.native/'release-staging.json';original=json.loads(path.read_text())
        for updates in ({'canaryPassed':False},{'sourceSha':'f'*40},{'renderSourceSha':'f'*40}):
            path.write_text(json.dumps(dict(original,**updates)))
            with self.subTest(updates=updates),self.assertRaises(ValueError):
                cleanup.current_native_record('staging',EVIDENCE)

    def test_native_artifact_drift_is_detected_before_cleanup(self):
        row=self.audited(3,'staging')
        Path(row['media']['artifact']).write_text('changed')
        with self.assertRaises(ValueError):cleanup.validate_native_record(row,'staging')

    def test_native_only_replay_is_idempotent_and_preserves_app_ledger(self):
        history=self.histories();row=self.proof(4,'staging')
        path=self.state/'rollback-ledger.json';path.write_text(json.dumps({'version':1,'projects':{p:[] for p in cleanup.PROJECTS},'nativeProjects':history}))
        receipt.append_receipt(self.state,'octospark-staging',None,row)
        first=path.read_bytes();receipt.append_receipt(self.state,'octospark-staging',None,row)
        self.assertEqual(path.read_bytes(),first)


    def test_changed_worker_configuration_refuses_stale_canary(self):
        self.proof(3,'staging')
        path=self.native/'env/media-staging.env';path.write_text(path.read_text()+'CHANGED=1\n')
        with self.assertRaisesRegex(ValueError,'configuration'):
            cleanup.current_native_record('staging',EVIDENCE)

    def test_changed_shared_render_configuration_refuses_cleanup(self):
        histories=self.histories()
        for env in histories:self.proof(3,env)
        (self.native/'env/render.env').write_text('RENDER_HOST_RENDERER_IMAGE=wrong\n')
        with self.assertRaisesRegex(ValueError,'renderer configuration'):
            cleanup.native_pins(histories)

    def test_native_history_is_part_of_plan_fingerprint(self):
        history=self.histories()
        for env in history:self.proof(3,env)
        apps={p:[{'images':{'API_IMAGE':f'app-{n}'}} for n in (1,2,3)] for p in cleanup.PROJECTS}
        ledger={'version':1,'projects':apps,'nativeProjects':history}
        path=self.state/'rollback-ledger.json';path.write_text(json.dumps(ledger))
        replacement=self.audited(4,'staging')
        refs={f'app-{n}' for n in (1,2,3)}|cleanup.native_record_refs(replacement)
        refs|={ref for rows in history.values() for row in rows for ref in cleanup.native_record_refs(row)}
        inventory=lambda kind:[] if kind=='container' else [{'RepoDigests':list(refs)}]
        with patch.object(cleanup,'validate_record'),patch.object(cleanup,'current_release',return_value=apps['octospark-prod'][-1]),patch.object(cleanup,'inspect_all',side_effect=inventory),patch.object(cleanup,'candidates',return_value=[]):
            before=cleanup.snapshot(self.state)
            ledger['nativeProjects']['staging'][0]=replacement;path.write_text(json.dumps(ledger))
            after=cleanup.snapshot(self.state)
            self.assertNotEqual(before['fingerprint'],after['fingerprint'])
            self.assertTrue(cleanup.native_record_refs(replacement)<=after['protected'])
            plan={'fingerprint':before['fingerprint'],'candidates':[{'Id':'candidate'}]}
            with patch.object(cleanup,'docker') as docker:
                with self.assertRaisesRegex(ValueError,'metadata'):
                    cleanup.apply(plan,self.state)
                docker.assert_not_called()

    def test_bootstrap_requires_all_native_images_and_never_replaces_history(self):
        history=self.histories()
        for env in history:self.proof(3,env)
        path=self.state/'rollback-ledger.json'
        apps={p:[] for p in cleanup.PROJECTS}
        path.write_text(json.dumps({'version':1,'projects':apps}))
        with patch.object(receipt.cleanup,'inspect_all',return_value=[]):
            with self.assertRaisesRegex(ValueError,'not all available'):
                receipt.bootstrap_native_history(self.state,history)
        self.assertNotIn('nativeProjects',json.loads(path.read_text()))
        refs={ref for rows in history.values() for row in rows for ref in cleanup.native_record_refs(row)}
        with patch.object(receipt.cleanup,'inspect_all',return_value=[{'RepoDigests':list(refs)}]):
            receipt.bootstrap_native_history(self.state,history)
        saved=json.loads(path.read_text());self.assertEqual(saved['projects'],apps)
        with self.assertRaisesRegex(ValueError,'already exists'):
            receipt.bootstrap_native_history(self.state,history)

    def test_native_only_mode_cannot_be_used_for_another_product(self):
        with self.assertRaises(ValueError):receipt.validate_inputs('trace-learn-staging',None,EVIDENCE,native_only=True)
        receipt.validate_inputs('octospark-staging',None,EVIDENCE,native_only=True)

if __name__=='__main__':unittest.main()
