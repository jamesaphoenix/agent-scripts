from pathlib import Path
import re
import unittest

BASE = Path(__file__).resolve().parents[1] / 'agent-loops/a-series-ci'
SLICES = ['jud-prod', 'jud-staging', 'jud-ci', 'jud-data', 'jud-observability']
GIB = 1024 ** 3


def parse(name):
    text = (BASE / 'slices' / (name + '.slice')).read_text()
    out = dict(re.findall(r'^(\w+)=(.+)$', text, re.M))
    return out


def size(value):
    number, unit = re.fullmatch(r'(\d+)([MG])', value).groups()
    return int(number) * (GIB if unit == 'G' else 1024 ** 2)


class Slices(unittest.TestCase):
    def test_budget_fits_with_at_least_8_gib_unallocated(self):
        total = sum(size(parse(n)['MemoryMax']) for n in SLICES)
        self.assertLessEqual(total, 51 * GIB)
        self.assertGreaterEqual(59 * GIB - total, 8 * GIB)

    def test_memory_high_is_below_max_and_proposed_values(self):
        expected = {'jud-prod': 20, 'jud-staging': 8, 'jud-data': 4, 'jud-observability': 1, 'jud-ci': 18}
        for name, gib in expected.items():
            data = parse(name)
            self.assertEqual(size(data['MemoryMax']), gib * GIB, name)
            self.assertLess(size(data['MemoryHigh']), size(data['MemoryMax']), name)

    def test_ci_has_the_lowest_cpu_weight(self):
        weights = {n: int(parse(n)['CPUWeight']) for n in SLICES}
        self.assertEqual(min(weights, key=weights.get), 'jud-ci')
        self.assertEqual(sum(1 for w in weights.values() if w == weights['jud-ci']), 1)

    def test_prod_and_data_do_not_swap(self):
        self.assertEqual(parse('jud-prod')['MemorySwapMax'], '0')
        self.assertEqual(parse('jud-data')['MemorySwapMax'], '0')

    def test_runner_template_uses_ci_slice_and_readme_states_it_is_a_proposal(self):
        self.assertIn('Slice=jud-ci.slice', (BASE / 'runner.service.template').read_text())
        self.assertIn('proposal', (BASE / 'slices/README.md').read_text().lower())
        for name in SLICES:
            self.assertIn('PROPOSAL', (BASE / 'slices' / (name + '.slice')).read_text())


if __name__ == '__main__':
    unittest.main()
