# Crawl graph

Create a private, standalone Three.js HTML atlas from saved tutor crawler artifacts. The exporter is read-only: it does not fetch websites, call paid providers, alter queues or start discovery workers.

The graph groups hostnames, merging only `www.`. Lines come from saved hyperlinks, explicit frontier parent relationships and cross-host redirects. Independent seeds stay disconnected. Positions are generated for readability and have no geographic meaning.

```sh
python3 agent-loops/tutor-lead-pathways/crawl_graph.py \
  --runtime-root /path/to/agent-scripts \
  --qualification-root /path/to/agent-scripts/state/qualification \
  --output /private/output/crawl-data.json

node agent-loops/tutor-lead-pathways/crawl-graph/build.mjs \
  --data /private/output/crawl-data.json \
  --three-root /path/to/verified/three-package \
  --esbuild-module /path/to/esbuild/lib/main.js \
  --out /private/output/crawl-graph.html

python3 -m unittest discover -s agent-loops/tutor-lead-pathways -p test_crawl_graph.py

node agent-loops/tutor-lead-pathways/crawl-graph/verify.mjs \
  --playwright-module /path/to/playwright/index.mjs \
  --html /private/output/crawl-graph.html \
  --output /private/output/qa
```

Use an integrity-verified Three.js package containing `build/three.module.js`, `examples/jsm/controls/OrbitControls.js` and `LICENSE`. The initial artifact uses Three.js 0.186.1. Build with the matching esbuild JavaScript package and native binary; unset an unrelated `ESBUILD_BINARY_PATH` override if present.

The HTML embeds its JavaScript, CSS and graph data. It works from `file://` without a server, CDN or API key. The full artifact hash manifest remains in the private `crawl-data.json` sidecar; the viewer includes its digest and sampled receipts. Keep both artifacts outside Git.

Search by domain or saved tutor name. Select a point or a connection to inspect pages, Decisions evidence and provenance. Switch between the full crawl and research neighbourhood; toggle utility domains, unlinked seeds and crawler tutor hints. Use the 2D/3D controls, fit, rotation, PNG export or JSON export. Keyboard shortcuts: `/` searches, `F` fits and `Escape` clears the selection.

The counts cover historical saved sources referenced by the qualification job. Observed domains, successful fetches, reviewed contacts and Decisions-qualified research profiles are different measures. A research-coloured directory is a source of profile evidence, not itself a verified independent tutor. Research profiles remain held for outreach. The utility-domain filter is a small explicit convenience list, not a classifier.

Open the final HTML in a WebGL-capable browser. When WebGL is unavailable, search and the domain inspector still work.
