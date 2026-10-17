"""Refresh embedded graph data in a hash-pinned, prebuilt offline viewer. No Node runtime needed."""
import argparse
import hashlib
import json
from pathlib import Path
import re

from tutor_graph_gate import atomic

DATA = re.compile(r'(<script id="graph-data" type="application/json">)(.*?)(</script>)', re.DOTALL)


def render(template, expected_hash, graph):
    if hashlib.sha256(template).hexdigest() != expected_hash:
        raise ValueError('Viewer template hash changed')
    text = template.decode('utf-8')
    if len(DATA.findall(text)) != 1:
        raise ValueError('Expected one embedded graph dataset')
    ids = {n['id'] for n in graph['nodes']}
    if len(ids) != len(graph['nodes']) or any(e['source'] not in ids or e['target'] not in ids for e in graph['edges']):
        raise ValueError('Invalid graph nodes or edges')
    payload = json.dumps(graph, ensure_ascii=False, separators=(',', ':'))
    for char, escaped in [('<', '\\u003c'), ('>', '\\u003e'), ('&', '\\u0026'), ('\u2028', '\\u2028'), ('\u2029', '\\u2029')]:
        payload = payload.replace(char, escaped)
    return DATA.sub(lambda m: m[1] + payload + m[3], text).encode('utf-8')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--template-sha256', required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    graph_bytes = args.data.read_bytes()
    output = render(args.template.read_bytes(), args.template_sha256, json.loads(graph_bytes))
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    changed = not args.output.exists() or args.output.read_bytes() != output
    if changed:
        temporary = args.output.with_name('.' + args.output.name + '.tmp')
        temporary.write_bytes(output)
        temporary.chmod(0o600)
        temporary.replace(args.output)
    result = {'viewerSha256': args.template_sha256, 'dataSha256': hashlib.sha256(graph_bytes).hexdigest(),
              'htmlSha256': hashlib.sha256(output).hexdigest(), 'changed': changed, 'networkCalls': 0}
    atomic(args.output.with_suffix('.build.json'), result)
    print(json.dumps(result))
