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
        self.assertEqual(200,c['processors']['batch/solar/metrics']['send_batch_max_size'])
        self.assertIn('filter/solar/metrics',c['service']['pipelines']['metrics/solar']['processors'])
        self.assertIn('tail_sampling/solar',c['service']['pipelines']['traces/solar']['processors'])
        self.assertEqual('/var/lib/solar-queue',c['extensions']['file_storage/solar']['directory'])

if __name__=='__main__': unittest.main()
