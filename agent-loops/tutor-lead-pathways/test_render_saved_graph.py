import hashlib
import importlib.util
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parent))
spec = importlib.util.spec_from_file_location('render_saved_graph', Path(__file__).with_name('render_saved_graph.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class RendererTests(unittest.TestCase):
    def setUp(self):
        self.template = b'<html><script id="graph-data" type="application/json">{}</script><script>code()</script></html>'
        self.sha = hashlib.sha256(self.template).hexdigest()
        self.graph = {'nodes': [{'id': 'tutor.test', 'name': '</script><script>bad()</script>'}], 'edges': []}

    def test_only_dataset_changes_and_embedded_html_is_escaped(self):
        html = module.render(self.template, self.sha, self.graph).decode()
        self.assertIn('\\u003c/script\\u003e', html)
        self.assertNotIn('<script>bad()', html)
        self.assertIn('<script>code()</script>', html)

    def test_wrong_template_hash_fails(self):
        with self.assertRaises(ValueError):
            module.render(self.template, '0' * 64, self.graph)

    def test_duplicate_nodes_fail(self):
        self.graph['nodes'].append(self.graph['nodes'][0])
        with self.assertRaises(ValueError):
            module.render(self.template, self.sha, self.graph)


if __name__ == '__main__':
    unittest.main()
