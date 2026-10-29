import unittest
from build import build

class Routes(unittest.TestCase):
    def test_routes_are_isolated_and_existing_pipelines_preserved(self):
        c=build()
        self.assertEqual('0.0.0.0:4322',c['receivers']['otlp/solar']['protocols']['http']['endpoint'])
        for name,pipeline in c['service']['pipelines'].items():
            if name.endswith('/solar') or name.endswith('/solar/self'):
                self.assertTrue(all('/solar' in e for e in pipeline['exporters']))
            else:
                self.assertTrue(all('/solar' not in e for e in pipeline['exporters']))
        for name in ('metrics/solar','metrics/solar/self'):
            self.assertIn('batch/solar/metrics',c['service']['pipelines'][name]['processors'])
        guard = str(c['processors']['filter/solar/self'])
        self.assertIn('otlp_http/solar/metrics', guard)
        self.assertIn('otlp/solar', guard)
        self.assertNotIn('== \"otlp_http/metrics\"', guard)
        self.assertEqual(200,c['processors']['batch/solar/metrics']['send_batch_max_size'])
        self.assertIn('filter/solar/metrics',c['service']['pipelines']['metrics/solar']['processors'])
        self.assertIn('tail_sampling/solar',c['service']['pipelines']['traces/solar']['processors'])
        for route in ('octospark-prod','octospark-staging','trace-learn-prod','trace-learn-staging'):
            project = c['exporters']['googlecloud/'+route]['project']
            self.assertIn({'key':'gcp.project.id','value':project,'action':'upsert'}, c['processors']['resource/'+route]['attributes'])
        self.assertEqual('/var/lib/solar-queue',c['extensions']['file_storage/solar']['directory'])

if __name__=='__main__': unittest.main()
