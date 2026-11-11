import ipaddress
import json
from pathlib import Path
import unittest

DAEMON = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci/daemon/daemon.json'
EXISTING = ['172.17.0.0/16', '172.18.0.0/16', '172.19.0.0/16', '172.20.0.0/16', '172.21.0.0/16',
            '172.22.0.0/16', '172.23.0.0/16', '172.24.0.0/16', '192.168.178.0/24', '100.64.0.0/10']


class Daemon(unittest.TestCase):
    def setUp(self):
        self.config = json.loads(DAEMON.read_text())

    def test_live_restore_and_bounded_logs(self):
        self.assertIs(self.config['live-restore'], True)
        self.assertEqual(self.config['log-driver'], 'json-file')
        self.assertEqual(self.config['log-opts'], {'max-size': '10m', 'max-file': '3'})

    def test_address_pool_avoids_existing_networks_and_lan(self):
        for pool in self.config['default-address-pools']:
            base = ipaddress.ip_network(pool['base'])
            self.assertGreaterEqual(pool['size'], base.prefixlen)
            for existing in EXISTING:
                self.assertFalse(base.overlaps(ipaddress.ip_network(existing)), existing)

    def test_no_surprising_keys(self):
        self.assertEqual(set(self.config), {'live-restore', 'log-driver', 'log-opts', 'default-address-pools'})


if __name__ == '__main__':
    unittest.main()
