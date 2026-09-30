from pathlib import Path
import json,os,subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class LaptopCleanupTests(unittest.TestCase):
 def run_cleanup(self,extra=None):
  with tempfile.TemporaryDirectory() as directory:
   folder=Path(directory);cli=folder/'docker';calls=folder/'calls.jsonl';envfile=folder/'bash-env'
   cli.write_text('''#!/usr/bin/env python3
import json,os,sys
args=sys.argv[1:]
with open(os.environ['FAKE_CALLS'],'a') as f:f.write(json.dumps(args)+'\\n')
if args==['context','show']:print('desktop-linux')
elif args==['buildx','prune','--help']:print('--max-used-space')
elif args[:2]==['buildx','prune']:
 if os.environ.get('FAKE_CACHE_FAIL')=='1':sys.exit(1)
 print('Total: 0B')
elif args[:2]==['volume','ls']:print('a'*64)
elif args[:2]==['volume','inspect']:print('2024-01-01T00:00:00Z')
''');cli.chmod(0o700);envfile.write_text('docker() { "$FAKE_DOCKER" "$@"; }\n')
   env=dict(os.environ,BASH_ENV=str(envfile),FAKE_DOCKER=str(cli),FAKE_CALLS=str(calls),DEV_DOCKER_CLEANUP_STATE_DIR=str(folder/'state'))
   env.pop('DEV_DOCKER_CLEANUP_PRUNE_ANONYMOUS_VOLUMES',None);env.update(extra or {})
   result=subprocess.run(['bash',str(ROOT/'agent-loops/dev-docker-cleanup/cleanup-dev-docker.sh')],env=env,capture_output=True,text=True)
   commands=[json.loads(line) for line in calls.read_text().splitlines()]
   report=folder/'state/last-summary.json'
   return result,commands,json.loads(report.read_text()) if report.exists() else None
 def test_default_preserves_even_old_anonymous_volumes_and_budgets_cache(self):
  result,commands,report=self.run_cleanup();self.assertEqual(result.returncode,0,result.stderr)
  self.assertFalse(any(c[:2] in (['volume','rm'],['volume','inspect']) for c in commands));self.assertFalse(report['anonymousVolumePruningEnabled'])
  prune=next(c for c in commands if c[:2]==['buildx','prune'] and '--help' not in c)
  self.assertIn('--max-used-space',prune);self.assertIn('10GB',prune);self.assertIn('desktop-linux',prune)
 def test_old_anonymous_volume_removal_requires_explicit_opt_in(self):
  result,commands,report=self.run_cleanup({'DEV_DOCKER_CLEANUP_PRUNE_ANONYMOUS_VOLUMES':'1'});self.assertEqual(result.returncode,0,result.stderr)
  self.assertTrue(any(c[:2]==['volume','rm'] for c in commands));self.assertEqual(report['anonymousVolumesRemoved'],1)
 def test_failed_cache_prune_stops_before_volume_cleanup(self):
  result,commands,report=self.run_cleanup({'FAKE_CACHE_FAIL':'1','DEV_DOCKER_CLEANUP_PRUNE_ANONYMOUS_VOLUMES':'1'})
  self.assertNotEqual(result.returncode,0);self.assertFalse(any(c[:2]==['volume','rm'] for c in commands))
if __name__=='__main__':unittest.main()
