import unittest
import json
from pathlib import Path
import sys
from build import build, compose, ROUTES, INGRESS

class Routes(unittest.TestCase):
    def test_shared_service_cannot_claim_the_legacy_solar_alias(self):
        compose=json.loads((Path(__file__).parent/'compose.synology.json').read_text())
        self.assertNotIn('otel-collector',compose['services'])
        self.assertEqual(['jud-otel'],compose['services']['host-collector']['networks']['solar']['aliases'])

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

    def test_shared_trace_pipelines_tail_sample_per_route(self):
        c=build()
        for route in ('octospark-prod','octospark-staging','trace-learn-prod','trace-learn-staging'):
            procs=c['service']['pipelines']['traces/'+route]['processors']
            sampler='tail_sampling/'+route
            # Per-trace state must never be shared between projects.
            self.assertEqual(1,sum(p.startswith('tail_sampling') for p in procs))
            self.assertLess(procs.index('filter/health_spans'),procs.index('filter/parentless_repository_spans'))
            self.assertLess(procs.index('filter/parentless_repository_spans'),procs.index(sampler))
            self.assertLess(procs.index(sampler),procs.index('batch'))
            policies={p['name']:p for p in c['processors'][sampler]['policies']}
            self.assertEqual(['ERROR'],policies['keep-errors']['status_code']['status_codes'])
            self.assertEqual(10,policies['sample-the-rest']['probabilistic']['sampling_percentage'])
        self.assertNotIn('tail_sampling',c['processors'])


class Minisforum(unittest.TestCase):
    def setUp(self):
        self.c = build('minisforum')
        self.syn = build()

    def test_one_collector_for_the_four_product_routes_and_no_solar(self):
        self.assertEqual({'docker_stats', 'hostmetrics'} | {'otlp/' + r for r in ROUTES}, set(self.c['receivers']))
        self.assertNotIn('solar', json.dumps(self.c))
        self.assertNotIn('synology', json.dumps(self.c))
        pipelines = self.c['service']['pipelines']
        self.assertEqual(12, len(pipelines))
        for route in ROUTES:
            for signal in ('traces', 'logs', 'metrics'):
                self.assertIn('%s/%s' % (signal, route), pipelines)
        endpoints = {r: self.c['receivers']['otlp/' + r]['protocols']['http']['endpoint'] for r in ROUTES}
        self.assertEqual({'octospark-staging': '0.0.0.0:4318', 'octospark-prod': '0.0.0.0:4319',
                          'trace-learn-staging': '0.0.0.0:4320', 'trace-learn-prod': '0.0.0.0:4321'}, endpoints)

    def test_routes_export_to_the_same_gcp_projects_as_synology(self):
        for route in ROUTES:
            for exporter in ('googlecloud/', 'googlemanagedprometheus/'):
                self.assertEqual(self.syn['exporters'][exporter + route]['project'], self.c['exporters'][exporter + route]['project'])
            self.assertIn({'key': 'gcp.project.id', 'value': self.c['exporters']['googlecloud/' + route]['project'], 'action': 'upsert'},
                          self.c['processors']['resource/' + route]['attributes'])
            # A route only ever exports to its own project.
            for signal in ('traces', 'logs'):
                self.assertEqual(['googlecloud/' + route], self.c['service']['pipelines']['%s/%s' % (signal, route)]['exporters'])
            self.assertEqual(['googlemanagedprometheus/' + route], self.c['service']['pipelines']['metrics/' + route]['exporters'])
        self.assertEqual(['tracelearn-staging', 'tracelearn-prod', 'octospark-staging', 'octospark-prod'],
                         [self.c['exporters']['googlecloud/' + r]['project'] for r in ('trace-learn-staging', 'trace-learn-prod', 'octospark-staging', 'octospark-prod')])

    def test_tail_sampling_per_route_matches_synology(self):
        for route in ROUTES:
            self.assertEqual(self.syn['processors']['tail_sampling/' + route], self.c['processors']['tail_sampling/' + route])
            procs = self.c['service']['pipelines']['traces/' + route]['processors']
            self.assertEqual(1, sum(p.startswith('tail_sampling') for p in procs))
            self.assertIn('tail_sampling/' + route, procs)

    def test_docker_stats_uses_api_1_44_and_a_container_name_filter_per_project(self):
        self.assertEqual('1.44', self.c['receivers']['docker_stats']['api_version'])
        self.assertEqual('1.41', self.syn['receivers']['docker_stats']['api_version'])
        for route in ROUTES:
            rule = self.c['processors']['filter/containers-' + route]['metrics']['datapoint'][0]
            self.assertIn('^/?%s-.*' % route, rule)
            self.assertIn('filter/containers-' + route, self.c['service']['pipelines']['metrics/' + route]['processors'])
            others = [r for r in ROUTES if r != route]
            self.assertTrue(all(('^/?%s-.*' % o) not in rule for o in others))

    def test_host_identity_and_limits(self):
        self.assertIn('minisforum', json.dumps(self.c['processors']['resource/octospark-prod']))
        self.assertEqual(640, self.c['processors']['memory_limiter']['limit_mib'])
        self.assertEqual('0.0.0.0:13133', self.c['extensions']['health_check']['endpoint'])

    def test_synology_profile_is_unchanged_by_the_new_one(self):
        self.assertEqual('0.0.0.0:4322', self.syn['receivers']['otlp/solar']['protocols']['http']['endpoint'])
        self.assertIn('synology-primary', json.dumps(self.syn))
        with self.assertRaises(ValueError):
            build('nope')

    def test_compose_limits_pin_slice_and_ports(self):
        service = compose('minisforum')['services']['host-collector']
        self.assertEqual('768m', service['mem_limit'])
        self.assertEqual(1, service['cpus'])
        self.assertEqual('576MiB', service['environment']['GOMEMLIMIT'])
        self.assertEqual('jud-observability.slice', service['cgroup_parent'])
        self.assertEqual(compose('synology')['services']['host-collector']['image'], service['image'])
        self.assertRegex(service['image'], r'@sha256:[0-9a-f]{64}$')
        self.assertEqual(['100.124.34.45:32018:4318', '100.124.34.45:32019:4319', '100.124.34.45:32118:4320',
                          '100.124.34.45:32119:4321', '127.0.0.1:32133:13133'], service['ports'])
        self.assertEqual(dict(zip(ROUTES, [(32018, 4318), (32019, 4319), (32118, 4320), (32119, 4321)])), INGRESS)
        self.assertEqual(['jud-otel'], service['networks']['telemetry']['aliases'])
        self.assertNotIn('otel-collector', compose('minisforum')['services'])

    def test_committed_compose_matches_the_generator(self):
        committed = json.loads((Path(__file__).parent / 'compose.minisforum.json').read_text())
        self.assertEqual(compose('minisforum'), committed)

    def test_compose_ports_pass_the_host_port_registry_and_slice_exists(self):
        base = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci'
        import importlib.util
        spec = importlib.util.spec_from_file_location('check_ports', base / 'check-ports.py')
        cp = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cp)
        path = Path(__file__).parent / 'compose.minisforum.json'
        self.assertEqual([], cp.check_compose(cp.load(base / 'ports.json'), path, 'jud-observability'))
        self.assertTrue((base / 'slices/jud-observability.slice').exists())

    def test_diff_against_deployed_reports_the_expected_changes(self):
        import diff_deployed_loader as loader
        deployed = {'current': {'target': 'minisforum-staging', 'release': '/srv/jud/observability/releases/abc',
                                'projects': {'octospark-staging': 'octospark-staging'}, 'source': {'commit': 'e' * 40}},
                    'service': {'image': compose('minisforum')['services']['host-collector']['image'], 'ports': ['127.0.0.1:32018->4318'],
                                'env': {'GOMAXPROCS': '2'}, 'cgroup_parent': ''}}
        text = loader.render(deployed['current'], deployed['service'], loader.normalise_compose(compose('minisforum')))
        self.assertIn('-    "127.0.0.1:32018->4318"', text)
        self.assertIn('+    "100.124.34.45:32019->4319"', text)
        self.assertIn('+  "cgroup_parent": "jud-observability.slice"', text)


if __name__=='__main__': unittest.main()
