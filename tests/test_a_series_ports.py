import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

BASE = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci'
spec = importlib.util.spec_from_file_location('check_ports', BASE / 'check-ports.py')
cp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cp)

REGISTRY = cp.load(BASE / 'ports.json')


def compose(text, suffix='.yml'):
    handle = tempfile.NamedTemporaryFile('w', suffix=suffix, delete=False)
    handle.write(text)
    handle.close()
    return handle.name


class Registry(unittest.TestCase):
    def test_shipped_registry_has_no_overlaps(self):
        self.assertEqual(cp.validate_registry(REGISTRY), [])

    def test_required_ports_are_registered_with_the_right_range(self):
        owners, _ = cp.table(REGISTRY)
        expected = {'ci-slots': [4100, 4302, 3510, 4510, 17233, 6100, 8302, 10100, 5510, 7510, 9510, 6510, 8510, 10510, 19233, 21233, 23233],
                    'ci-services': [15432, 15433, 16379, 16380], 'staging-apis': [32080, 32090, 32180],
                    'prod-apis': [32081, 32091, 32181], 'collector': [32018, 32019, 32118, 32119, 32133],
                    'staging-databases': [32432, 32433]}
        for range_name, ports in expected.items():
            for port in ports:
                self.assertEqual(owners[port]['range'], range_name, port)

    def test_overlap_is_detected(self):
        bad = json.loads(json.dumps(REGISTRY))
        bad['ranges'][1]['ports']['4100'] = {'project': 'x'}
        problems = cp.validate_registry(bad)
        self.assertTrue(any('port 4100 is in both ci-slots and ci-services' in p for p in problems))

    def test_offsets_expand_each_base(self):
        ports = cp.expand({'name': 'n', 'base': [[10, 11], [50, 50]], 'offsets': [0, 100], 'projects': ['p'], 'bind': 'any'})
        self.assertEqual(sorted(ports), [10, 11, 50, 110, 111, 150])

    def test_main_exit_codes(self):
        self.assertEqual(cp.main([]), 0)
        self.assertEqual(cp.main(['--who', '32080']), 0)


class Parse(unittest.TestCase):
    def test_short_syntax_forms(self):
        self.assertEqual(cp.parse_port('127.0.0.1:32080:4000'), [('127.0.0.1', 32080)])
        self.assertEqual(cp.parse_port('32080:4000/tcp'), [('', 32080)])
        self.assertEqual(cp.parse_port('4000'), [])
        self.assertEqual(cp.parse_port('[::1]:32080:4000'), [('::1', 32080)])
        self.assertEqual(cp.parse_port('8000-8001:9000-9001'), [('', 8000), ('', 8001)])
        self.assertEqual(cp.parse_port('${API_PORT}:4000'), [])

    def test_long_syntax(self):
        self.assertEqual(cp.parse_port({'target': 4000, 'published': '32080', 'host_ip': '127.0.0.1'}), [('127.0.0.1', 32080)])
        self.assertEqual(cp.parse_port({'target': 4000}), [])


class Compose(unittest.TestCase):
    def test_valid_compose_passes(self):
        path = compose('services:\n  api:\n    image: x\n    ports:\n      - "127.0.0.1:32080:4000"\n  mcp:\n    ports:\n      - 127.0.0.1:32090:8787\n')
        self.assertEqual(cp.check_compose(REGISTRY, path, 'octospark-staging'), [])

    def test_other_projects_port_is_rejected(self):
        path = compose('services:\n  api:\n    ports:\n      - "127.0.0.1:32180:4000"\n')
        problems = cp.check_compose(REGISTRY, path, 'octospark-staging')
        self.assertEqual(len(problems), 1)
        self.assertIn('belongs to trace-learn-staging', problems[0])

    def test_unregistered_port_and_missing_bind_address_are_rejected(self):
        path = compose('services:\n  api:\n    ports:\n      - "9999:4000"\n      - "32081:4000"\n')
        problems = cp.check_compose(REGISTRY, path, 'octospark-prod')
        self.assertTrue(any('9999' in p and 'not in ports.json' in p for p in problems))
        self.assertTrue(any('32081' in p and 'loopback' in p for p in problems))

    def test_tailscale_range_accepts_the_tailscale_ip(self):
        path = compose('services:\n  postgres:\n    ports:\n      - "100.124.34.45:32432:5432"\n')
        self.assertEqual(cp.check_compose(REGISTRY, path, 'octospark-staging-data'), [])
        path = compose('services:\n  postgres:\n    ports:\n      - "0.0.0.0:32432:5432"\n')
        self.assertEqual(len(cp.check_compose(REGISTRY, path, 'octospark-staging-data')), 1)

    def test_ci_slot_ports_match_ci_projects_only(self):
        path = compose('services:\n  s:\n    ports:\n      - "127.0.0.1:6100:4100"\n')
        self.assertEqual(cp.check_compose(REGISTRY, path, 'octospark-ci'), [])
        self.assertEqual(len(cp.check_compose(REGISTRY, path, 'octospark-prod')), 1)

    def test_json_compose_and_ports_without_publish(self):
        path = compose(json.dumps({'services': {'redis': {'ports': ['6379']}}}), '.json')
        self.assertEqual(cp.check_compose(REGISTRY, path, 'octospark-ci'), [])

    def test_compose_main_exit_status(self):
        path = compose('services:\n  api:\n    ports:\n      - "9999:1"\n')
        self.assertEqual(cp.main(['--compose', path, '--project', 'octospark-prod']), 1)


class Listening(unittest.TestCase):
    def test_wide_ci_listener_is_flagged_and_unregistered_ignored(self):
        lines = ['LISTEN 0 4096 0.0.0.0:15432 0.0.0.0:*', 'LISTEN 0 4096 [::]:16379 [::]:*',
                 'LISTEN 0 4096 127.0.0.1:32080 0.0.0.0:*', 'LISTEN 0 128 0.0.0.0:22 0.0.0.0:*',
                 'LISTEN 0 4096 100.124.34.45:32018 0.0.0.0:*']
        problems = cp.check_listening(REGISTRY, lines)
        self.assertEqual(len(problems), 2)
        self.assertTrue(all('requires loopback' in p for p in problems))


if __name__ == '__main__':
    unittest.main()
