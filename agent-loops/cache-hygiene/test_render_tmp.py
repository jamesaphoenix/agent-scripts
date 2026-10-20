import hashlib,importlib.util,json,os,subprocess,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('render_tmp',Path(__file__).with_name('render_tmp.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
DAY=86400
IDLE=lambda:([],[],[],False)
NO_AGENTS=lambda:('absent',[])

def age(path,days):
    """Backdate a whole tree; ctime cannot be set, so tests pass a future `now` instead."""
    t=time.time()-days*DAY
    for base,dirs,files in os.walk(path):
        for name in dirs+files:os.utime(os.path.join(base,name),(t,t),follow_symlinks=False)
    os.utime(path,(t,t))

class RenderTmpRetention(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name).resolve()
        self.root=self.base/'render-tmp';self.job=self.root/'film-1';self.state=self.base/'state'
        for rel in ('workflow/frames/frame-0001.png','node_modules/pkg/index.js','delivery/final.mp4','evidence/receipt.json','manifest.json'):
            p=self.job/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'x'*64)
        self.cache=self.job/'app/node_modules/.cache/webpack/default-development';self.cache.mkdir(parents=True);(self.cache/'0.pack').write_bytes(b'p'*64)
    def tearDown(self):self.temp.cleanup()
    def run_cleanup(self,days=8,**kwargs):
        options=dict(snapshot_fn=IDLE,herdr_fn=NO_AGENTS,now=time.time()+days*DAY,apply=True);options.update(kwargs)
        return m.run([self.root],[self.root],self.state,**options)

    def test_idle_job_drops_intermediates_and_keeps_delivery_evidence_and_job_files(self):
        r=self.run_cleanup()
        self.assertFalse((self.job/'workflow').exists());self.assertFalse((self.job/'node_modules').exists());self.assertFalse((self.job/'app').exists())
        for kept in ('delivery/final.mp4','evidence/receipt.json','manifest.json'):self.assertTrue((self.job/kept).exists(),kept)
        delivery=[x for x in r['rows'] if x['kind']=='render-delivery'];self.assertEqual(delivery[0]['result'],'retained_delivery_recent')
        self.assertGreater(r['removed_bytes'],0)

    def test_dry_run_deletes_nothing(self):
        r=self.run_cleanup(apply=False);self.assertTrue((self.job/'workflow/frames/frame-0001.png').exists());self.assertIn('dry_run',r['results'])

    def test_recent_write_keeps_job_active(self):
        r=self.run_cleanup(days=1);self.assertTrue((self.job/'workflow').exists());self.assertEqual(r['job_detail'][0]['reason'],'recent_write')

    def test_keep_marker_herdr_cwd_and_open_handle_each_protect_the_job(self):
        (self.job/'KEEP').touch();r=self.run_cleanup();self.assertEqual(r['job_detail'][0]['reason'],'keep_marker');(self.job/'KEEP').unlink()
        r=self.run_cleanup(herdr_fn=lambda:('complete',[m.key(self.job/'workflow')]));self.assertEqual(r['job_detail'][0]['reason'],'herdr_agent')
        r=self.run_cleanup(snapshot_fn=lambda:([m.key(self.job/'node_modules/pkg/index.js')],[],[],False));self.assertEqual(r['job_detail'][0]['reason'],'open_handle_or_cwd')
        self.assertTrue((self.job/'workflow/frames/frame-0001.png').exists())

    def test_nested_delivery_is_kept_while_its_siblings_expire(self):
        nested=self.job/'workflow/step-2/delivery/cut.mp4';nested.parent.mkdir(parents=True);nested.write_bytes(b'final')
        self.run_cleanup();self.assertTrue(nested.exists());self.assertFalse((self.job/'workflow/frames').exists())

    def test_delivery_requires_matching_r2_receipt(self):
        delivery=self.job/'delivery';digest=hashlib.sha256(b'x'*64).hexdigest()
        r=self.run_cleanup(days=31);self.assertTrue(delivery.exists())
        self.assertEqual([x['result'] for x in r['rows'] if x['kind']=='render-delivery'],['retained_delivery_no_r2_receipt'])
        receipt={'store':'r2','status':'verified','bucket':'media','files':[{'path':'final.mp4','sha256':'0'*64,'key':'films/final.mp4'}]}
        (delivery/m.RECEIPT).write_text(json.dumps(receipt));r=self.run_cleanup(days=31);self.assertTrue(delivery.exists())
        self.assertEqual([x['result'] for x in r['rows'] if x['kind']=='render-delivery'],['retained_delivery_hash_unconfirmed'])
        receipt['files'][0]['sha256']=digest;(delivery/m.RECEIPT).write_text(json.dumps(receipt))
        self.run_cleanup(days=31);self.assertFalse(delivery.exists());self.assertTrue((self.job/'manifest.json').exists())

    def test_symlink_from_active_job_protects_shared_sibling(self):
        shared=self.root/'shared/node_modules';shared.mkdir(parents=True);(shared/'dep.js').write_text('x')
        active=self.root/'film-2';active.mkdir();(active/'node_modules').symlink_to(shared)
        r=self.run_cleanup(herdr_fn=lambda:('complete',[m.key(active)]))
        self.assertTrue((shared/'dep.js').exists());self.assertIn(('active','symlink_from_active_job'),[(x['status'],x.get('reason')) for x in r['job_detail'] if x['job']==str(self.root/'shared')])

    def test_expiring_symlink_unit_never_touches_its_target(self):
        outside=self.base/'outside';outside.mkdir();(outside/'keep.txt').write_text('x');(self.job/'linked').symlink_to(outside)
        self.run_cleanup();self.assertFalse((self.job/'linked').is_symlink());self.assertTrue((outside/'keep.txt').exists())

    def test_webpack_cache_expires_after_three_days_before_intermediates(self):
        age(self.cache,4);r=self.run_cleanup(days=2.5)
        self.assertFalse(self.cache.exists());self.assertTrue((self.job/'workflow').exists())
        self.assertEqual([x['result'] for x in r['rows'] if x['kind']=='webpack-cache'],['removed'])

    def test_webpack_cap_removes_oldest_first(self):
        newer=self.cache.with_name('default-production');newer.mkdir();(newer/'0.pack').write_bytes(b'p'*64)
        age(self.cache,0.5);cap=m.cache_units(newer.parent)[0 if m.cache_units(newer.parent)[0]['path']==str(newer) else 1]['bytes']
        r=self.run_cleanup(days=2.1,cache_cap_bytes=cap)
        self.assertFalse(self.cache.exists());self.assertTrue(newer.exists())
        self.assertEqual(sorted((x['path'],x['result']) for x in r['rows'] if x['kind']=='webpack-cache'),[(str(self.cache),'removed'),(str(newer),'retained_within_cap')])

    def test_busy_project_cache_outside_job_roots_is_retained(self):
        runtime=self.base/'agent-runtime/repo';cache=runtime/'node_modules/.cache/webpack/default-development';cache.mkdir(parents=True);(cache/'0.pack').write_bytes(b'p')
        age(cache,5);r=m.run([self.root],[runtime.parent],self.state,True,snapshot_fn=lambda:([],['node '+str(runtime)+'/render.ts'],[],False),herdr_fn=NO_AGENTS,now=time.time()+DAY)
        self.assertTrue(cache.exists());self.assertIn('retained_process_command',r['results'])
        r=m.run([self.root],[runtime.parent],self.state,True,snapshot_fn=IDLE,herdr_fn=NO_AGENTS,now=time.time()+DAY);self.assertFalse(cache.exists())

    def test_herdr_absent_or_stopped_means_no_agents_but_errors_refuse(self):
        self.assertEqual(m.herdr_cwds(str(self.base/'missing-herdr')),('absent',[]))
        stub=self.base/'herdr'
        def respond(payload,code):stub.write_text(f"#!/bin/sh\necho '{json.dumps(payload)}'\nexit {code}\n");stub.chmod(0o700)
        respond({'error':{'code':'server_not_running'}},1);self.assertEqual(m.herdr_cwds(str(stub)),('server_not_running',[]))
        respond({'result':{'agents':[{'cwd':'/Users/x/render-tmp/A','foreground_cwd':'/Users/x/render-tmp/A/b'}]}},0)
        self.assertEqual(m.herdr_cwds(str(stub)),('complete',['/users/x/render-tmp/a','/users/x/render-tmp/a/b']))
        respond({'error':{'code':'permission_denied'}},1)
        with self.assertRaises(RuntimeError):m.herdr_cwds(str(stub))

if __name__=='__main__':unittest.main()
