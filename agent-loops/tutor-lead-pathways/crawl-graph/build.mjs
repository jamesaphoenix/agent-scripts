#!/usr/bin/env node
import { readFile, writeFile, mkdir, chmod } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const option = key => {
  const i = args.indexOf(key);
  if (i < 0 || !args[i + 1] || args[i + 1].startsWith('--')) throw new Error(`Missing ${key}`);
  return resolve(args[i + 1]);
};
const dataPath = option('--data');
const threeRoot = option('--three-root');
const out = option('--out');
const esbuild = await import(pathToFileURL(option('--esbuild-module')).href);
const graph = JSON.parse(await readFile(dataPath, 'utf8'));
if (graph.schemaVersion !== 1 || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) throw new Error('Unsupported graph schema');
const ids = new Set(graph.nodes.map(n => n.id));
if (ids.size !== graph.nodes.length || graph.edges.some(e => !ids.has(e.source) || !ids.has(e.target))) throw new Error('Graph has duplicate nodes or missing endpoints');
// Keep the full manifest in the private JSON sidecar. The HTML carries its hash and sampled provenance.
const manifest = graph.meta.artifactManifest;
graph.meta.artifactManifestSha256 = createHash('sha256').update(JSON.stringify(manifest ?? {})).digest('hex');
delete graph.meta.artifactManifest;
graph.meta.evidenceSampling = 'Up to six page URLs and six observation receipts per domain, and two evidence samples per domain connection. Full artifact hashes remain in crawl-data.json.';
graph.nodes = graph.nodes.map(n => ({ ...n, urls: n.urls.slice(0, 6), events: n.events.slice(0, 6) }));
graph.edges = graph.edges.map(e => ({ ...e, evidence: e.evidence.slice(0, 2) }));
const bundle = await esbuild.build({
  entryPoints: [resolve(here, 'viewer.js')], bundle: true, write: false,
  format: 'iife', platform: 'browser', target: ['chrome109', 'safari16'],
  minify: true, legalComments: 'inline',
  alias: { 'three': resolve(threeRoot, 'build/three.module.js'), 'three/addons/controls/OrbitControls.js': resolve(threeRoot, 'examples/jsm/controls/OrbitControls.js') },
});
const data = JSON.stringify(graph).replace(/</g, '\\u003c').replace(/>/g, '\\u003e').replace(/&/g, '\\u0026').replace(/\u2028/g, '\\u2028').replace(/\u2029/g, '\\u2029');
const [template, css, license] = await Promise.all(['index.template.html', 'viewer.css'].map(name => readFile(resolve(here, name), 'utf8')).concat(readFile(resolve(threeRoot, 'LICENSE'), 'utf8')));
const html = template.replace('/*__STYLES__*/', () => css).replace('/*__DATA__*/', () => data)
  .replace('/*__SCRIPT__*/', () => `/* Three.js MIT license\n${license.replaceAll('*/', '* /')}\n*/\n${bundle.outputFiles[0].text.replace(/<\/script/gi, '<\\/script')}`);
await mkdir(dirname(out), { recursive: true, mode: 0o700 });
await writeFile(out, html, { mode: 0o600 });
await chmod(out, 0o600);
console.log(JSON.stringify({ output: out, bytes: Buffer.byteLength(html), nodes: graph.nodes.length, edges: graph.edges.length, threeVersion: JSON.parse(await readFile(resolve(threeRoot, 'package.json'), 'utf8')).version }));
