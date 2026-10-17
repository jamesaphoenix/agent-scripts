import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const $ = id => document.getElementById(id);
const data = JSON.parse($('graph-data').textContent);
const number = n => Number(n).toLocaleString('en-GB');
const date = value => value ? new Date(value).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'Europe/London' }) + ' UK' : 'Not recorded';
const palette = { research: '#65efce', crawled: '#7b9cff', held: '#e8af68', discovered: '#647695' };
const categories = { research: 'Research evidence', crawled: 'Fetched domain', held: 'Held / failed only', discovered: 'Discovered, not fetched' };
const edgeNames = { hyperlink: 'Recorded hyperlink', redirect: 'Cross-domain redirect', crawl_path: 'Recorded crawl parent' };
const nodes = new Map(data.nodes.map(n => [n.id, n]));
const adjacency = new Map(data.nodes.map(n => [n.id, []]));
data.edges.forEach(e => { adjacency.get(e.source).push(e); adjacency.get(e.target).push(e); });
const utilityRoots = ['facebook.com', 'instagram.com', 'youtube.com', 'youtu.be', 'twitter.com', 'x.com', 'linkedin.com', 'google.com', 'google.co.uk', 'gstatic.com', 'googleapis.com', 'maps.app.goo.gl', 'wa.me', 'whatsapp.com', 'tiktok.com', 'pinterest.com', 'wordpress.com', 'wix.com', 'squarespace.com', 'w3.org', 'adobe.com', 'apple.com', 'microsoft.com', 'gravatar.com', 'paypal.com', 'stripe.com', 'addtoany.com', 'vimeo.com', 'policies.google.com'];
const isUtility = id => utilityRoots.some(root => id === root || id.endsWith('.' + root));
const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
let selected = null, selectedEdge = null, view = 'atlas', flat = false, visible = [], visibleEdges = [], positions = new Map();
let renderer, scene, camera, controls, cloud, lines, highlighted, stars, animation, hoverIndex = null;
let pointAttributes, labels = [], forced = new Set(), pendingFit = true, dirty = true;
const viewport = $('viewport');
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
const project = new THREE.Vector3();

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = text;
  return el;
}
function safeUrl(value) {
  try { const u = new URL(value); return ['http:', 'https:'].includes(u.protocol) && !u.username && !u.password ? u.href : null; } catch { return null; }
}
function externalLink(text, url, className = '') {
  const el = element('a', className, text);
  const safe = safeUrl(url);
  if (safe) { el.href = safe; el.target = '_blank'; el.rel = 'noopener noreferrer'; }
  return el;
}
function button(text, className, callback) {
  const el = element('button', className, text);
  el.type = 'button'; el.addEventListener('click', callback); return el;
}
function section(parent, text) { parent.append(element('div', 'section-label', text)); }
function detail(parent, label, value) { const row = element('div', 'detail-row'); row.append(element('span', '', label), element('span', '', value)); parent.append(row); }
function download(filename, blob) {
  const url = URL.createObjectURL(blob), a = element('a'); a.href = url; a.download = filename;
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 5000);
}
function hash(str) { let h = 2166136261; for (let i = 0; i < str.length; i++) h = Math.imul(h ^ str.charCodeAt(i), 16777619); return h >>> 0; }
function random(seed) { return () => { seed = Math.imul(seed ^ seed >>> 15, seed | 1); seed ^= seed + Math.imul(seed ^ seed >>> 7, seed | 61); return ((seed ^ seed >>> 14) >>> 0) / 4294967296; }; }

// Connected components stay separate. No layout-only edge is added to the evidence graph.
function layout(list, edges) {
  const index = new Map(list.map((n, i) => [n.id, i]));
  const parent = list.map((_, i) => i);
  const root = i => { while (parent[i] !== i) { parent[i] = parent[parent[i]]; i = parent[i]; } return i; };
  const pairs = edges.map(e => [index.get(e.source), index.get(e.target)]);
  for (const [a, b] of pairs) parent[root(a)] = root(b);
  const groups = new Map();
  list.forEach((n, i) => { const r = root(i); if (!groups.has(r)) groups.set(r, []); groups.get(r).push(i); });
  const components = [...groups.values()].sort((a, b) => b.length - a.length || list[a[0]].id.localeCompare(list[b[0]].id));
  const pos = new Float32Array(list.length * 3), vel = new Float32Array(list.length * 3), anchors = new Float32Array(list.length * 3);
  const golden = Math.PI * (3 - Math.sqrt(5));
  const groupOf = new Map();
  let linkedIndex = 0, isolatedIndex = 0;
  const isolatedCount = components.filter(g => g.length === 1).length;
  const coreRadius = Math.max(70, Math.pow(components[0]?.length || 1, .37) * 13);
  for (let g = 0; g < components.length; g++) {
    const members = components[g], size = members.length;
    let ax = 0, ay = 0, az = 0;
    if (size === 1) {
      const j = isolatedIndex++, y = 1 - 2 * (j + .5) / Math.max(1, isolatedCount), r = Math.sqrt(1 - y * y);
      const radius = coreRadius * 1.65 + 60 + (j % 7) * 7;
      ax = Math.cos(j * golden) * r * radius; ay = y * radius * .72; az = Math.sin(j * golden) * r * radius;
    } else if (g > 0) {
      const j = ++linkedIndex, angle = j * golden, radius = coreRadius * (1.08 + .32 * (j % 13) / 12);
      ax = Math.cos(angle) * radius; ay = Math.sin(j * 2.1) * radius * .45; az = Math.sin(angle) * radius * .75;
    }
    const spread = Math.max(13, Math.pow(size, .37) * 13);
    members.forEach((i, j) => {
      groupOf.set(i, members);
      const rng = random(hash(list[i].id)), y = rng() * 2 - 1, theta = rng() * Math.PI * 2, r = Math.sqrt(1 - y * y) * spread;
      pos[i * 3] = ax + Math.cos(theta) * r; pos[i * 3 + 1] = ay + y * spread; pos[i * 3 + 2] = az + Math.sin(theta) * r;
      anchors[i * 3] = ax; anchors[i * 3 + 1] = ay; anchors[i * 3 + 2] = az;
      if (size === 1) { pos[i * 3] = ax; pos[i * 3 + 1] = ay; pos[i * 3 + 2] = az; }
    });
  }
  const rng = random(71823);
  for (let iteration = 0; iteration < 145; iteration++) {
    for (let i = 0; i < list.length; i++) {
      const members = groupOf.get(i), p = i * 3;
      if (members.length < 2) continue;
      for (let k = 0; k < 9; k++) {
        const j = members[Math.floor(rng() * members.length)]; if (j === i) continue;
        const q = j * 3, dx = pos[p] - pos[q], dy = pos[p + 1] - pos[q + 1], dz = pos[p + 2] - pos[q + 2];
        const distance2 = dx * dx + dy * dy + dz * dz + 30;
        const force = Math.min(6, (90 + members.length * 42) / distance2) / Math.sqrt(distance2);
        vel[p] += dx * force; vel[p + 1] += dy * force; vel[p + 2] += dz * force;
      }
      for (let axis = 0; axis < 3; axis++) vel[p + axis] -= (pos[p + axis] - anchors[p + axis]) * .002;
    }
    for (const [a, b] of pairs) {
      const p = a * 3, q = b * 3, dx = pos[q] - pos[p], dy = pos[q + 1] - pos[p + 1], dz = pos[q + 2] - pos[p + 2];
      const length = Math.sqrt(dx * dx + dy * dy + dz * dz) || 1;
      const force = (length - 38) * .012 / length;
      for (const [axis, delta] of [[0, dx], [1, dy], [2, dz]]) { vel[p + axis] += delta * force; vel[q + axis] -= delta * force; }
    }
    const cooling = 1 - iteration / 180;
    for (let i = 0; i < pos.length; i++) { vel[i] *= .72; pos[i] += Math.max(-8, Math.min(8, vel[i])) * cooling; }
  }
  return new Map(list.map((n, i) => [n.id, new THREE.Vector3(pos[i * 3], pos[i * 3 + 1], flat ? 0 : pos[i * 3 + 2])]));
}

function researchSet() {
  const set = new Set();
  data.nodes.filter(n => n.category === 'research').forEach(n => {
    set.add(n.id); for (const e of adjacency.get(n.id)) { set.add(e.source); set.add(e.target); }
  });
  return set;
}
const researchIds = researchSet();
function passes(n) {
  return $('show-' + n.category).checked &&
    (!$('hide-utilities').checked || !isUtility(n.id)) &&
    ($('show-isolated').checked || adjacency.get(n.id).length > 0 || view === 'research' && n.category === 'research') &&
    (!$('tutor-only').checked || n.tutorSignal || n.category === 'research') &&
    (view === 'atlas' || researchIds.has(n.id));
}
function dispose(object) {
  if (!object) return;
  scene.remove(object); object.geometry?.dispose(); object.material?.dispose();
}
function rebuild({ fit = true } = {}) {
  visible = data.nodes.filter(n => passes(n) || forced.has(n.id));
  const visibleIds = new Set(visible.map(n => n.id));
  visibleEdges = data.edges.filter(e => visibleIds.has(e.source) && visibleIds.has(e.target));
  $('visible-count').textContent = `${number(visible.length)} / ${number(data.nodes.length)} domains · ${number(visibleEdges.length)} connections`;
  $('empty').hidden = visible.length > 0;
  $('view-title').textContent = view === 'research' ? 'Where the research led.' : 'Follow the crawl.';
  $('view-description').textContent = data.meta.tutorOnly ? 'Model-qualified local tutor sites. Only recorded connections are shown.' : view === 'research' ? 'Domains with qualified profile evidence, plus their recorded neighbours.' : 'Recorded links between observed websites. Unlinked seeds are hidden by default.';
  if (!renderer) return;
  positions = layout(visible, visibleEdges);
  dispose(cloud); dispose(lines); dispose(highlighted);
  const xyz = new Float32Array(visible.length * 3), colors = new Float32Array(visible.length * 3), sizes = new Float32Array(visible.length), alpha = new Float32Array(visible.length);
  visible.forEach((n, i) => { positions.get(n.id).toArray(xyz, i * 3); new THREE.Color(palette[n.category]).toArray(colors, i * 3); sizes[i] = n.category === 'research' ? 10 : n.category === 'crawled' ? 6 : n.category === 'held' ? 4.8 : 4; alpha[i] = 1; });
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute('position', new THREE.BufferAttribute(xyz, 3));
  geometry.setAttribute('color', new THREE.BufferAttribute(colors, 3));
  geometry.setAttribute('size', new THREE.BufferAttribute(sizes, 1));
  geometry.setAttribute('alpha', new THREE.BufferAttribute(alpha, 1));
  pointAttributes = { alpha, sizes };
  cloud = new THREE.Points(geometry, new THREE.ShaderMaterial({
    transparent: true, depthWrite: false, vertexColors: true, blending: THREE.AdditiveBlending,
    uniforms: { pixelRatio: { value: renderer.getPixelRatio() } },
    vertexShader: `attribute float size; attribute float alpha; varying vec3 vColor; varying float vAlpha; uniform float pixelRatio; void main(){vColor=color;vAlpha=alpha;vec4 mv=modelViewMatrix*vec4(position,1.0);gl_Position=projectionMatrix*mv;gl_PointSize=clamp(size*(950.0/max(1.0,-mv.z)),size*0.6,32.0)*pixelRatio;}`,
    fragmentShader: `varying vec3 vColor; varying float vAlpha; void main(){float r=length(gl_PointCoord-0.5)*2.0;if(r>1.0)discard;float a=exp(-r*r*5.0)*0.65+(1.0-smoothstep(0.0,0.38,r))*0.7;gl_FragColor=vec4(vColor,a*vAlpha);}`,
  }));
  cloud.frustumCulled = false; scene.add(cloud);
  const linePositions = new Float32Array(visibleEdges.length * 6), lineColors = new Float32Array(visibleEdges.length * 6);
  visibleEdges.forEach((e, i) => {
    positions.get(e.source).toArray(linePositions, i * 6); positions.get(e.target).toArray(linePositions, i * 6 + 3);
    const c = new THREE.Color(e.kind === 'redirect' ? '#b599dd' : nodes.get(e.source).category === 'research' || nodes.get(e.target).category === 'research' ? '#438779' : '#3a648b');
    c.toArray(lineColors, i * 6); c.toArray(lineColors, i * 6 + 3);
  });
  const lg = new THREE.BufferGeometry(); lg.setAttribute('position', new THREE.BufferAttribute(linePositions, 3)); lg.setAttribute('color', new THREE.BufferAttribute(lineColors, 3));
  lines = new THREE.LineSegments(lg, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .30, depthWrite: false })); scene.add(lines);
  updateHighlight(); makeLabels(); dirty = true;
  if (fit) fitGraph();
  pendingFit = false;
}
function updateHighlight() {
  if (!renderer || !cloud) return;
  dirty = true;
  const neighbourhood = new Set(selected ? [selected] : []);
  const relevant = selected ? visibleEdges.filter(e => e.source === selected || e.target === selected) : [];
  relevant.forEach(e => { neighbourhood.add(e.source); neighbourhood.add(e.target); });
  visible.forEach((n, i) => { pointAttributes.alpha[i] = selected ? neighbourhood.has(n.id) ? 1 : .13 : n.category === 'discovered' ? .55 : .95; });
  cloud.geometry.getAttribute('alpha').needsUpdate = true;
  lines.material.opacity = selected ? .035 : .30;
  dispose(highlighted);
  const points = [], colors = [];
  relevant.forEach(e => {
    const color = new THREE.Color(e.source === selected ? '#65efce' : '#a7b8ff');
    const source = positions.get(e.source), target = positions.get(e.target);
    const segment = (a, b) => { a.toArray(points, points.length); b.toArray(points, points.length); color.toArray(colors, colors.length); color.toArray(colors, colors.length); };
    segment(source, target);
    const direction = target.clone().sub(source).normalize();
    const normal = new THREE.Vector3().crossVectors(direction, Math.abs(direction.z) < .9 ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(0, 1, 0)).normalize();
    const length = Math.min(7, source.distanceTo(target) * .16);
    const tip = target.clone().addScaledVector(direction, -2);
    const base = tip.clone().addScaledVector(direction, -length);
    segment(tip, base.clone().addScaledVector(normal, length * .4));
    segment(tip, base.clone().addScaledVector(normal, -length * .4));
  });
  const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.Float32BufferAttribute(points, 3)); g.setAttribute('color', new THREE.Float32BufferAttribute(colors, 3));
  highlighted = new THREE.LineSegments(g, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, opacity: .85, depthWrite: false })); scene.add(highlighted);
}
function makeLabels() {
  $('graph-labels').replaceChildren(); labels = [];
  const candidates = selected ? [nodes.get(selected), ...visible.filter(n => n.id !== selected && (adjacency.get(selected) || []).some(e => e.source === n.id || e.target === n.id)).sort((a, b) => b.fetchedPages - a.fetchedPages).slice(0, 8)]
    : [...visible].sort((a, b) => (b.category === 'research') - (a.category === 'research') || (b.inDegree + b.outDegree) - (a.inDegree + a.outDegree)).slice(0, 11);
  candidates.forEach(n => {
    if (!positions.has(n.id)) return;
    const el = element('span', `graph-label ${n.category === 'research' ? 'research' : ''} ${selected === n.id ? 'selected' : ''}`, n.id);
    $('graph-labels').append(el); labels.push({ el, id: n.id });
  });
}
function fitGraph(ids) {
  if (!renderer || !positions.size) return;
  const box = new THREE.Box3();
  for (const [id, p] of positions) if (!ids || ids.has(id)) box.expandByPoint(p);
  if (box.isEmpty()) return;
  const sphere = box.getBoundingSphere(new THREE.Sphere());
  const width = viewport.clientWidth, height = viewport.clientHeight;
  const vertical = THREE.MathUtils.degToRad(camera.fov / 2), horizontal = Math.atan(Math.tan(vertical) * width / height * (width > 800 ? .72 : .92));
  const distance = Math.max(80, sphere.radius / Math.sin(Math.min(vertical, horizontal)) * 1.12);
  const direction = flat ? new THREE.Vector3(0, 0, 1) : new THREE.Vector3(.12, .12, 1).normalize();
  camera.position.copy(sphere.center).addScaledVector(direction, distance); camera.near = .1; camera.far = Math.max(10000, distance * 8); camera.updateProjectionMatrix();
  controls.target.copy(sphere.center); controls.update();
  dirty = true;
}
function focusSelection() {
  if (!selected) return;
  const ids = new Set([selected]); adjacency.get(selected).forEach(e => { ids.add(e.source); ids.add(e.target); }); fitGraph(ids);
}
function select(id, edge = null, focus = true) {
  selected = id; selectedEdge = edge;
  $('tooltip').hidden = true;
  if (id && !visible.some(n => n.id === id)) {
    forced = new Set([id]); adjacency.get(id).forEach(e => { forced.add(e.source); forced.add(e.target); }); rebuild({ fit: false });
  }
  updateHighlight(); makeLabels(); renderInspector();
  if (focus && id) focusSelection();
}
function clearSelection() { selected = null; selectedEdge = null; const hadForced = forced.size > 0; forced.clear(); if (hadForced) rebuild(); else { updateHighlight(); makeLabels(); } renderInspector(); }
function connectionButton(e, id) {
  const outgoing = e.source === id, other = outgoing ? e.target : e.source;
  const b = button('', 'connection', () => select(other, e));
  b.title = `${e.source} → ${e.target}: ${edgeNames[e.kind]}`;
  const text = element('span', 'connection-text', other); text.append(element('small', '', edgeNames[e.kind]));
  b.append(element('span', 'direction', outgoing ? '↗' : '↙'), text, element('span', 'weight', number(e.weight)));
  return b;
}
function renderInspector() {
  const content = $('inspector-content'); content.replaceChildren();
  $('close-inspector').hidden = !selected;
  if (!selected) {
    const body = element('div', 'empty-inspector');
    body.append(element('div', 'inspect-glyph', '⌘'), element('h3', '', 'Inspect a domain.'), element('p', '', 'Select a point to see its pages, recorded connections and research evidence.'));
    body.append(element('div', 'suggestion-label', 'A FEW PLACES TO START'));
    const starters = ['inclusivemindstutoring.co.uk', 'thetutorsassociation.org.uk', 'tutorperch.com', 'firsttutors.com'];
    const picks = starters.map(id => nodes.get(id)).filter(Boolean);
    if (!picks.some(n => n.profiles.some(p => /inclusive minds/i.test(p.name || '')))) {
      const inclusive = data.nodes.find(n => n.profiles.some(p => /inclusive minds/i.test(p.name || ''))); if (inclusive) picks.unshift(inclusive);
    }
    picks.slice(0, 4).forEach(n => {
      const b = button('', 'connection', () => select(n.id));
      const text = element('span', 'connection-text', n.id); text.append(element('small', '', n.category === 'research' ? 'Decisions profile evidence' : `${number(adjacency.get(n.id).length)} recorded connections`)); b.append(element('span', 'direction', '↗'), text); body.append(b);
    });
    body.append(element('p', 'evidence-note', 'Historical crawl coverage includes off-topic links. Use the tutor signal filter to narrow the map; it is a crawler hint, not a qualification decision.'));
    content.append(body); return;
  }
  const n = nodes.get(selected), body = element('div', 'inspector-body');
  body.append(element('h3', '', n.id), element('span', 'badge ' + n.category, categories[n.category]));
  const blurb = n.category === 'research' ? 'Saved Decisions-qualified profile evidence references this domain. Directory pages may represent several tutors.' : n.category === 'held' ? 'Saved requests were held or failed. No successful fetch is recorded for this hostname.' : n.category === 'discovered' ? 'A recorded link or frontier entry names this domain. A successful page fetch is not recorded.' : 'Successful page fetches are recorded. Fetching a website does not establish a local, independent tutor.';
  body.append(element('p', 'description', blurb));
  const stats = element('div', 'inspect-metrics');
  for (const [value, text] of [[n.fetchedPages, 'pages fetched'], [n.textPages, 'with text'], [n.heldPages, 'held / failed']]) { const m = element('div'); m.append(element('strong', '', number(value)), element('span', '', text)); stats.append(m); }
  body.append(stats);
  const actions = element('div', 'inspect-actions'); actions.append(button('Focus neighbourhood', '', focusSelection), externalLink('Visit site ↗', n.urls[0]?.url || 'https://' + n.id)); body.append(actions);
  if (selectedEdge) {
    const e = selectedEdge, receipt = e.evidence[0], box = element('div', 'selected-evidence');
    box.append(element('strong', '', `${e.source} → ${e.target}`), element('div', '', edgeNames[e.kind]));
    if (receipt) { box.append(externalLink('From: ' + receipt.sourceUrl, receipt.sourceUrl), externalLink('To: ' + receipt.targetUrl, receipt.targetUrl)); if (receipt.anchor) box.append(element('div', '', `Anchor: ${receipt.anchor}`)); box.append(element('div', '', `Observed ${date(receipt.observedAt)}`), element('div', '', `Source: ${receipt.artifact}`)); }
    body.append(box);
  }
  if (n.profiles.length) {
    section(body, `DECISIONS RESEARCH · ${number(n.profiles.length)}`);
    n.profiles.slice(0, 12).forEach(p => {
      const card = element('div', 'profile-card'); card.append(element('strong', '', p.name || 'Unnamed profile'));
      card.append(element('p', '', [p.decision?.replaceAll('_', ' '), p.locality].filter(Boolean).join(' · ') || 'Saved Decisions result'));
      card.append(element('p', '', 'Model-qualified research · outreach held'));
      if (p.decisionKey) card.append(element('p', '', 'Decision: ' + p.decisionKey)); body.append(card);
    });
    if (n.profiles.length > 12) body.append(element('p', 'evidence-note', `${number(n.profiles.length - 12)} more profiles are included in the exported graph data.`));
  }
  const connections = [...adjacency.get(n.id)].sort((a, b) => b.weight - a.weight);
  section(body, `CONNECTIONS · ${number(connections.length)}`);
  body.append(element('p', 'evidence-note', '↗ Outgoing · ↙ Incoming · number = saved observations'));
  let count = 0;
  const list = element('div');
  const more = button('Show more connections', 'inspect-more', () => appendConnections());
  function appendConnections() { connections.slice(count, count + 10).forEach(e => list.append(connectionButton(e, n.id))); count += 10; more.hidden = count >= connections.length; }
  appendConnections(); body.append(list, more);
  if (!connections.length) body.append(element('p', 'evidence-note', 'No cross-domain connections were saved. Independent seeds are not joined artificially.'));
  section(body, 'SAVED PAGE SAMPLES');
  if (!n.urls.length) body.append(element('p', 'evidence-note', 'Link-only evidence. No fetched page receipt is available.'));
  n.urls.forEach(p => {
    const a = externalLink(p.title || p.url, p.url, 'page-evidence');
    a.append(element('small', '', p.url), element('small', '', [p.fetched ? 'Fetched' : 'Not recorded as fetched', p.text ? 'Text saved' : null, p.held ? 'Also has a held / failed request' : null].filter(Boolean).join(' · '))); body.append(a);
  });
  if (n.errors.length) { section(body, 'HELD / FAILED REQUESTS'); n.errors.forEach(e => body.append(element('p', 'error-line', `${e.reason} (${number(e.count)} observations)`))); }
  section(body, 'PROVENANCE');
  detail(body, 'First observation', date(n.firstSeen)); detail(body, 'Latest observation', date(n.lastSeen));
  detail(body, 'Sources', n.sources.join(', ') || 'Recorded link'); detail(body, 'Saved passes', number(n.runs.length));
  detail(body, 'Tutor signal', n.tutorSignal ? 'Crawler hint present' : 'Not recorded');
  detail(body, 'Reviewed profile references', number(n.reviewedProfileCount));
  if (n.events.length) {
    const receipt = n.events[n.events.length - 1];
    const sample = element('div', 'selected-evidence'); sample.append(element('div', '', 'Sample observation receipt'), element('div', '', receipt.artifact), element('div', '', `${receipt.outcome} · ${date(receipt.observedAt)}`)); body.append(sample);
  }
  content.append(body);
}

function search() {
  const term = $('search').value.trim().toLowerCase(); $('clear-search').hidden = !term;
  const results = $('search-results'); results.replaceChildren(); results.hidden = !term;
  if (!term) return;
  const matches = data.nodes.map(n => ({ n, rank: n.id === term ? 0 : n.id.startsWith(term) ? 1 : n.id.includes(term) ? 2 : n.profiles.some(p => (p.name || '').toLowerCase().includes(term)) ? 3 : 9 })).filter(x => x.rank < 9).sort((a, b) => a.rank - b.rank || b.n.profiles.length - a.n.profiles.length || a.n.id.localeCompare(b.n.id));
  for (const { n } of matches.slice(0, 7)) {
    const b = button('', 'search-result', () => { select(n.id); results.hidden = true; });
    b.append(element('span', '', n.id), element('small', '', n.profiles[0]?.name || categories[n.category])); results.append(b);
  }
  if (!matches.length) results.append(element('p', '', 'No saved domain or profile matches.'));
  if (matches.length > 7) results.append(element('p', '', `${number(matches.length)} matches. Keep typing to narrow them.`));
}
function resetFilters() {
  for (const cat of Object.keys(categories)) $('show-' + cat).checked = true;
  $('show-isolated').checked = Boolean(data.meta.tutorOnly); $('hide-utilities').checked = true; $('tutor-only').checked = false;
  forced.clear(); selected = null; selectedEdge = null; rebuild(); renderInspector();
}
function resize() {
  if (!renderer) return;
  const w = viewport.clientWidth, h = viewport.clientHeight;
  renderer.setSize(w, h); camera.aspect = w / h;
  // Leave breathing room for the inspector while keeping raycasts aligned with the canvas.
  camera.setViewOffset(w, h, w > 800 ? w * .115 : 0, 0, w, h); camera.updateProjectionMatrix();
  dirty = true;
  if (pendingFit) fitGraph();
}
function setupScene() {
  renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, preserveDrawingBuffer: true, powerPreference: 'high-performance' });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.setClearColor(0x090f1b, 0); renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.domElement.setAttribute('aria-label', '3D domain graph. Use search or the domain inspector to explore with the keyboard.');
  $('scene').append(renderer.domElement);
  scene = new THREE.Scene(); camera = new THREE.PerspectiveCamera(44, 1, .1, 20000); camera.position.set(0, 0, 1400);
  controls = new OrbitControls(camera, renderer.domElement); controls.enableDamping = true; controls.dampingFactor = .08; controls.minDistance = 20; controls.maxDistance = 12000; controls.autoRotateSpeed = .25; controls.enableRotate = true;
  const positions = [], rng = random(92643);
  for (let i = 0; i < 650; i++) positions.push((rng() - .5) * 7000, (rng() - .5) * 7000, (rng() - .5) * 7000 - 1400);
  const starGeometry = new THREE.BufferGeometry(); starGeometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3));
  stars = new THREE.Points(starGeometry, new THREE.PointsMaterial({ color: '#7697bd', size: 1.2, transparent: true, opacity: .17, depthWrite: false })); scene.add(stars);
  resize(); new ResizeObserver(resize).observe(viewport);
  let down = null;
  renderer.domElement.addEventListener('pointerdown', e => { down = { x: e.clientX, y: e.clientY }; });
  renderer.domElement.addEventListener('pointermove', e => {
    if (!cloud || e.buttons) { $('tooltip').hidden = true; return; }
    const rect = renderer.domElement.getBoundingClientRect(); pointer.set((e.clientX - rect.left) / rect.width * 2 - 1, -(e.clientY - rect.top) / rect.height * 2 + 1);
    raycaster.params.Points.threshold = Math.max(2, camera.position.distanceTo(controls.target) / rect.height * 4);
    raycaster.setFromCamera(pointer, camera);
    const intersections = raycaster.intersectObject(cloud, false);
    // Prefer the closest point to the pointer ray, then the nearest depth.
    intersections.sort((a, b) => a.distanceToRay - b.distanceToRay || a.distance - b.distance);
    hoverIndex = intersections[0]?.index ?? null;
    renderer.domElement.style.cursor = hoverIndex !== null ? 'pointer' : 'grab';
    const tooltip = $('tooltip'); tooltip.hidden = hoverIndex === null;
    if (hoverIndex !== null) {
      const n = visible[hoverIndex]; tooltip.replaceChildren(element('strong', '', n.id), element('small', '', `${categories[n.category]} · ${number(n.fetchedPages)} fetched pages`));
      tooltip.style.left = Math.max(8, Math.min(e.clientX - rect.left + 14, rect.width - 290)) + 'px'; tooltip.style.top = Math.max(10, Math.min(e.clientY - rect.top + 14, rect.height - 95)) + 'px';
    }
  });
  renderer.domElement.addEventListener('pointerleave', () => { $('tooltip').hidden = true; hoverIndex = null; });
  renderer.domElement.addEventListener('pointerup', e => { if (down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5 && hoverIndex !== null) select(visible[hoverIndex].id, null, false); down = null; });
  function animate() {
    animation = requestAnimationFrame(animate);
    const changed = controls.update();
    if (changed || dirty) {
      renderer.render(scene, camera); dirty = false;
      const w = viewport.clientWidth, h = viewport.clientHeight, occupied = [];
      labels.forEach(({ el, id }) => {
        project.copy(positionsFor(id)).project(camera);
        const x = (project.x * .5 + .5) * w, y = (-project.y * .5 + .5) * h;
        const collides = occupied.some(p => Math.abs(p.x - x) < 155 && Math.abs(p.y - y) < 22);
        const hide = project.z > 1 || project.z < -1 || x < 0 || x > w - 80 || y < 145 || y > h - 70 || collides;
        el.hidden = hide; if (!hide) { el.style.left = x + 'px'; el.style.top = y + 'px'; occupied.push({ x, y }); }
      });
    }
  }
  animate();
}
function positionsFor(id) { return positions.get(id) || new THREE.Vector3(); }
function setDimension(isFlat) {
  flat = isFlat; $('mode-2d').classList.toggle('active', flat); $('mode-3d').classList.toggle('active', !flat);
  $('mode-2d').setAttribute('aria-pressed', String(flat)); $('mode-3d').setAttribute('aria-pressed', String(!flat));
  if (controls) { controls.enableRotate = !flat; if (flat) { controls.autoRotate = false; $('rotate').setAttribute('aria-pressed', 'false'); } }
  $('rotate').disabled = flat;
  rebuild();
}

function populateAbout() {
  const body = $('about-content');
  body.append(element('p', '', 'This is a historical snapshot of saved tutor discovery artifacts, not a live crawler and not a list of qualified contacts. It includes domains encountered outside the target niche or area.'));
  body.append(element('p', '', data.meta.coverage));
  const dl = element('dl');
  for (const [k, v] of [['Snapshot', date(data.meta.capturedAt)], ['Saved Go checkpoints', number(data.meta.checkpointFiles)], ['Read errors', number(data.meta.readErrors.length)], ['Grouping', data.meta.grouping], ['Link weights', 'Distinct saved source/target URL observations, with their timestamps. Not SEO authority or backlink counts.'], ['Research evidence', `${number(data.meta.modelQualifiedProfiles)} model-qualified profiles, ${number(data.meta.profilesWithDecisions)} with saved Decisions results. Profiles and domains are different units.`], ['Reviewed contacts', `${number(data.meta.reviewedContacts)} in referenced exports. Not added to the research profile count.`], ['Sampled receipts', data.meta.evidenceSampling], ['Privacy', data.meta.privacy], ['Artifact manifest hash', data.meta.artifactManifestSha256]]) { dl.append(element('dt', '', k), element('dd', '', v)); }
  body.append(dl, element('p', 'about-divider', 'A point is one hostname. A line is an observed cross-domain relationship. Distance and position are generated for readability and have no geographic or statistical meaning. Decorative background stars are not data points.'));
  body.append(element('p', '', 'Built with Three.js. The JavaScript, graph data and styles are embedded in this file. No external service is called when you open it.'));
}
function wireControls() {
  if (data.meta.tutorOnly) {
    $('show-isolated').checked = true;
    document.querySelector('.heading p').textContent = 'Qualified local tutor websites.';
  }
  $('total-domains').textContent = number(data.meta.nodeCount); $('total-links').textContent = number(data.meta.edgeCount);
  $('total-fetched').textContent = number(data.meta.domainsFetched); $('total-pages').textContent = number(data.meta.fetchedPages);
  $('qualified-count').textContent = number(data.meta.modelQualifiedProfiles);
  $('snapshot-date').textContent = new Date(data.meta.capturedAt).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', timeZone: 'Europe/London' });
  $('snapshot-date').title = date(data.meta.capturedAt);
  for (const cat of Object.keys(categories)) $('count-' + cat).textContent = number(data.nodes.filter(n => n.category === cat).length);
  $('search').addEventListener('input', search);
  $('search').addEventListener('keydown', e => { if (e.key === 'Enter') $('search-results').querySelector('button')?.click(); });
  $('clear-search').addEventListener('click', () => { $('search').value = ''; search(); $('search').focus(); });
  document.querySelectorAll('[data-view]').forEach(b => b.addEventListener('click', () => {
    view = b.dataset.view; selected = null; selectedEdge = null; forced.clear();
    document.querySelectorAll('[data-view]').forEach(x => { x.classList.toggle('active', x === b); x.setAttribute('aria-pressed', String(x === b)); }); rebuild(); renderInspector();
  }));
  document.querySelectorAll('.layers input,.options input').forEach(x => x.addEventListener('change', () => { selected = null; selectedEdge = null; forced.clear(); rebuild(); renderInspector(); }));
  $('reset-filters').addEventListener('click', resetFilters); $('empty-reset').addEventListener('click', resetFilters);
  $('close-inspector').addEventListener('click', clearSelection);
  $('mode-3d').addEventListener('click', () => setDimension(false)); $('mode-2d').addEventListener('click', () => setDimension(true));
  $('fit').addEventListener('click', () => fitGraph());
  $('rotate').addEventListener('click', () => { if (controls && !flat) { controls.autoRotate = !controls.autoRotate; $('rotate').setAttribute('aria-pressed', String(controls.autoRotate)); } });
  $('export-png').addEventListener('click', () => { if (renderer) { renderer.render(scene, camera); renderer.domElement.toBlob(blob => { if (blob) download('tutor-crawl-atlas.png', blob); }, 'image/png'); } });
  $('export-data').addEventListener('click', () => download('tutor-crawl-graph.json', new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })));
  $('about-button').addEventListener('click', () => $('about-dialog').showModal()); $('close-about').addEventListener('click', () => $('about-dialog').close());
  $('about-dialog').addEventListener('click', e => { if (e.target === $('about-dialog')) { const r = e.target.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) e.target.close(); } });
  window.addEventListener('keydown', e => {
    if (e.key === 'Escape') { $('search-results').hidden = true; clearSelection(); }
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement || e.ctrlKey || e.metaKey || e.altKey || $('about-dialog').open) return;
    if (e.key === '/') { e.preventDefault(); $('search').focus(); }
    if (e.key.toLowerCase() === 'f') fitGraph();
  });
  window.addEventListener('pagehide', () => { cancelAnimationFrame(animation); controls?.dispose(); renderer?.dispose(); });
}

wireControls(); populateAbout(); renderInspector();
// Yield once so the local file can paint its loading state before preparing the graph.
requestAnimationFrame(() => setTimeout(() => {
  try { setupScene(); }
  catch (error) { console.warn('WebGL viewer unavailable:', error.message); $('webgl-error').hidden = false; $('mode-2d').disabled = $('mode-3d').disabled = $('fit').disabled = $('rotate').disabled = $('export-png').disabled = true; renderer = null; }
  rebuild(); $('loading').hidden = true;
  document.documentElement.dataset.ready = 'true';
  document.documentElement.dataset.motion = reducedMotion ? 'reduced' : 'standard';
}, 40));
