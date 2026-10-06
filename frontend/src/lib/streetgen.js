// Random street network generator (pure JS, no React). Everything is in metres.
//
// How it works (ELI5):
//  1. Lay a (rotated) lattice of intersections over a city-shaped blob.
//  2. Nudge them with random jitter + smooth "warp" noise so streets stop being perfectly straight.
//  3. Decide which lattice edges become roads: first a random spanning tree (guarantees everything is
//     connected, creates dead ends), then extra edges kept with probability `connectivity`
//     (1 = full grid like La Plata, low = tree-like organic town).
//  4. Main roads: every K-th lattice line, diagonal avenues, and "radial" roads from the centre to the edge.
//  5. Edges get a gentle bend (quadratic curve) for the organic feel.

export const DEFAULTS = {
  seed: 1,
  sizeKm: 4, block: 110, rotation: 0, squareness: 0, shape: 0.15,
  jitter: 0.1, warp: 0.1, curve: 0.05, connectivity: 0.95, falloff: 0.3, diagRandom: 0,
  avenueEvery: 6, diagAves: 0, radials: 0,
};

export const PRESETS = [
  { key: "laplata", label: "La Plata (planned grid)", params: {
    seed: 1, sizeKm: 5, block: 120, rotation: 0, squareness: 1, shape: 0, jitter: 0, warp: 0, curve: 0,
    connectivity: 1, falloff: 0, diagRandom: 0, avenueEvery: 6, diagAves: 1, radials: 0 } },
  { key: "kenya", label: "Kenyan town (organic)", params: {
    seed: 7, sizeKm: 2.5, block: 85, rotation: 23, squareness: 0, shape: 0.45, jitter: 0.5, warp: 0.9, curve: 0.5,
    connectivity: 0.3, falloff: 0.75, diagRandom: 0.1, avenueEvery: 0, diagAves: 0, radials: 5 } },
  { key: "old", label: "Old town (medieval)", params: {
    seed: 3, sizeKm: 1.6, block: 60, rotation: 10, squareness: 0, shape: 0.35, jitter: 0.6, warp: 1, curve: 0.7,
    connectivity: 0.55, falloff: 0.5, diagRandom: 0.25, avenueEvery: 0, diagAves: 0, radials: 6 } },
  { key: "sprawl", label: "Suburban sprawl", params: {
    seed: 5, sizeKm: 6, block: 160, rotation: 12, squareness: 0.2, shape: 0.3, jitter: 0.15, warp: 0.4, curve: 0.35,
    connectivity: 0.45, falloff: 0.6, diagRandom: 0, avenueEvery: 5, diagAves: 0, radials: 0 } },
];

export function randomParams() {
  const r = (a, b) => a + Math.random() * (b - a);
  const q = (v, st) => Math.round(v / st) * st;
  const planned = Math.random() < 0.4;
  return {
    seed: Math.floor(Math.random() * 99999),
    sizeKm: q(r(1.5, 6), 0.5), block: q(r(60, 180), 5), rotation: Math.round(r(0, 90)),
    squareness: planned ? q(r(0.5, 1), 0.05) : q(r(0, 0.3), 0.05), shape: q(r(0, 0.5), 0.05),
    jitter: planned ? q(r(0, 0.2), 0.05) : q(r(0.2, 0.7), 0.05),
    warp: planned ? q(r(0, 0.3), 0.05) : q(r(0.3, 1), 0.05),
    curve: planned ? q(r(0, 0.2), 0.05) : q(r(0.2, 0.8), 0.05),
    connectivity: planned ? q(r(0.85, 1), 0.05) : q(r(0.25, 0.7), 0.05),
    falloff: q(r(0, 0.8), 0.05), diagRandom: planned ? 0 : q(r(0, 0.3), 0.05),
    avenueEvery: planned ? Math.round(r(4, 8)) : Math.random() < 0.3 ? Math.round(r(4, 8)) : 0,
    diagAves: planned && Math.random() < 0.5 ? Math.round(r(1, 3)) : 0,
    radials: planned ? 0 : Math.round(r(0, 7)),
  };
}

// ── small helpers ─────────────────────────────────────────────────────────────
function mulberry32(a) {
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash2(x, y, s) {
  let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ Math.imul(s, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
const smooth = (t) => t * t * (3 - 2 * t);
/** Smooth value noise in [-1, 1]. `l` = wavelength in metres. */
function noise(x, y, seed, l) {
  x /= l; y /= l;
  const ix = Math.floor(x), iy = Math.floor(y), fx = smooth(x - ix), fy = smooth(y - iy);
  const a = hash2(ix, iy, seed), b = hash2(ix + 1, iy, seed), c = hash2(ix, iy + 1, seed), d = hash2(ix + 1, iy + 1, seed);
  const top = a + (b - a) * fx, bot = c + (d - c) * fx;
  return (top + (bot - top) * fy) * 2 - 1;
}
const mod = (a, k) => ((a % k) + k) % k;

class Heap {
  constructor() { this.k = []; this.v = []; }
  get size() { return this.k.length; }
  push(key, val) {
    const k = this.k, v = this.v;
    let i = k.length;
    k.push(key); v.push(val);
    while (i > 0) {
      const p = (i - 1) >> 1;
      if (k[p] <= key) break;
      k[i] = k[p]; v[i] = v[p]; i = p;
    }
    k[i] = key; v[i] = val;
  }
  pop() {
    const k = this.k, v = this.v;
    const top = v[0], lk = k.pop(), lv = v.pop();
    if (k.length) {
      let i = 0;
      const n = k.length;
      for (;;) {
        let c = 2 * i + 1;
        if (c >= n) break;
        if (c + 1 < n && k[c + 1] < k[c]) c++;
        if (k[c] >= lk) break;
        k[i] = k[c]; v[i] = v[c]; i = c;
      }
      k[i] = lk; v[i] = lv;
    }
    return top;
  }
}

// ── generator ─────────────────────────────────────────────────────────────────
export function generate(p) {
  const rnd = mulberry32(Math.imul(p.seed | 0, 2654435761) ^ 0x9e3779b9);
  const s = p.block;
  const R = p.sizeKm * 500; // sizeKm is the diameter
  const K = Math.round(p.avenueEvery);
  const rot = (p.rotation * Math.PI) / 180, cr = Math.cos(rot), sr = Math.sin(rot);
  const pw = 2 + p.squareness * 8; // superellipse exponent: 2 = circle, 10 = almost square
  const ph = [rnd() * 6.283, rnd() * 6.283, rnd() * 6.283];
  const outline = (t) => 0.5 * Math.sin(2 * t + ph[0]) + 0.3 * Math.sin(3 * t + ph[1]) + 0.2 * Math.sin(5 * t + ph[2]);
  const [nA, nB, nC, nD, nE] = Array.from({ length: 5 }, () => (rnd() * 1e6) | 0);
  const dens = (i, j) => Math.max(0, 1 - p.falloff * Math.min(Math.hypot(i * s, j * s) / R, 1.3) ** 2);

  // 1. lattice nodes inside the city outline
  const half = Math.ceil((R * (1 + p.shape)) / s) + 1;
  const N = 2 * half + 1;
  const slot = (i, j) => (i + half) * N + (j + half);
  const id = new Int32Array(N * N).fill(-1);
  const nodes = [];
  for (let i = -half; i <= half; i++) {
    for (let j = -half; j <= half; j++) {
      const x = i * s, y = j * s;
      const r = (Math.abs(x / R) ** pw + Math.abs(y / R) ** pw) ** (1 / pw);
      if (r > 1 + p.shape * outline(Math.atan2(y, x))) continue;
      const rx = x * cr - y * sr, ry = x * sr + y * cr;
      const wx = rx + p.warp * s * (1.6 * noise(rx, ry, nA, s * 12) + 0.4 * noise(rx, ry, nB, s * 4));
      const wy = ry + p.warp * s * (1.6 * noise(rx, ry, nC, s * 12) + 0.4 * noise(rx, ry, nD, s * 4));
      id[slot(i, j)] = nodes.length;
      nodes.push({ x: wx + (rnd() - 0.5) * 0.9 * p.jitter * s, y: wy + (rnd() - 0.5) * 0.9 * p.jitter * s, i, j });
    }
  }
  const areaKm2 = (nodes.length * s * s) / 1e6;
  const nid = (i, j) => (i < -half || i > half || j < -half || j > half ? -1 : id[slot(i, j)]);

  // 2. candidate edges
  const mainEdges = [], plain = [], diags = [];
  const cellUsed = new Set();
  const step = Math.max(4, (K > 0 ? K : 6) + (K % 2)); // even, so the two diagonal families never share a cell
  const offs = [0, 1, -1, 2, -2, 3, -3].slice(0, Math.round(p.diagAves)).map((o) => o * step);
  const D = new Set(offs);
  nodes.forEach((n, a) => {
    const { i, j } = n;
    const r = nid(i + 1, j), u = nid(i, j + 1);
    if (r >= 0) (K > 0 && mod(j, K) === 0 ? mainEdges : plain).push({ a, b: r, main: K > 0 && mod(j, K) === 0 });
    if (u >= 0) (K > 0 && mod(i, K) === 0 ? mainEdges : plain).push({ a, b: u, main: K > 0 && mod(i, K) === 0 });
    if (D.has(i - j)) {
      const t = nid(i + 1, j + 1);
      if (t >= 0) { mainEdges.push({ a, b: t, main: true }); cellUsed.add(slot(i, j)); }
    }
    if (D.has(i + j)) {
      const t = nid(i + 1, j - 1);
      if (t >= 0) { mainEdges.push({ a, b: t, main: true }); cellUsed.add(slot(i, j - 1)); }
    }
  });
  if (p.diagRandom > 0) {
    nodes.forEach((n) => {
      const { i, j } = n;
      if (cellUsed.has(slot(i, j)) || rnd() >= p.diagRandom * dens(i + 0.5, j + 0.5)) return;
      const flip = rnd() < 0.5;
      const a = flip ? nid(i, j + 1) : nid(i, j), b = flip ? nid(i + 1, j) : nid(i + 1, j + 1);
      if (a >= 0 && b >= 0) diags.push({ a, b, main: false });
    });
  }

  // 3. choose roads: spanning tree first (connected + dead ends), then extras by probability
  const par = Int32Array.from({ length: nodes.length }, (_, k) => k);
  const find = (x) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  const union = (a, b) => { par[find(a)] = find(b); };
  const keep = [];
  for (const e of mainEdges) { union(e.a, e.b); keep.push(e); }
  for (let k = plain.length - 1; k > 0; k--) { const m = Math.floor(rnd() * (k + 1)); [plain[k], plain[m]] = [plain[m], plain[k]]; }
  for (const e of plain) {
    const A = nodes[e.a], B = nodes[e.b];
    if (find(e.a) !== find(e.b)) { union(e.a, e.b); keep.push(e); }
    else if (rnd() < p.connectivity * dens((A.i + B.i) / 2, (A.j + B.j) / 2)) keep.push(e);
  }
  for (const e of diags) { union(e.a, e.b); keep.push(e); }

  // keep only the biggest connected piece
  const size = new Map();
  for (let k = 0; k < nodes.length; k++) { const r = find(k); size.set(r, (size.get(r) || 0) + 1); }
  let best = -1, bestN = 0;
  for (const [r, c] of size) if (c > bestN) { best = r; bestN = c; }
  const remap = new Int32Array(nodes.length).fill(-1);
  const out = [];
  nodes.forEach((n, k) => { if (find(k) === best) { remap[k] = out.length; out.push({ x: n.x, y: n.y, deg: 0 }); } });
  const edges = keep.filter((e) => remap[e.a] >= 0 && remap[e.b] >= 0)
    .map((e) => ({ a: remap[e.a], b: remap[e.b], main: e.main }));
  if (!out.length) return { nodes: [], edges: [], bounds: { minx: -1, miny: -1, maxx: 1, maxy: 1 }, stats: null };

  // 4. radial main roads: shortest paths from the centre out to the edge of town
  const n = out.length;
  const adj = Array.from({ length: n }, () => []);
  edges.forEach((e, k) => {
    const w = Math.hypot(out[e.a].x - out[e.b].x, out[e.a].y - out[e.b].y);
    adj[e.a].push([e.b, k, w]); adj[e.b].push([e.a, k, w]);
  });
  const nRad = Math.round(p.radials);
  if (nRad > 0) {
    let c = 0, cd = Infinity;
    out.forEach((q, k) => { const d = q.x * q.x + q.y * q.y; if (d < cd) { cd = d; c = k; } });
    const dist = new Float64Array(n).fill(Infinity), prevN = new Int32Array(n).fill(-1), prevE = new Int32Array(n).fill(-1);
    const done = new Uint8Array(n), h = new Heap();
    dist[c] = 0; h.push(0, c);
    while (h.size) {
      const u = h.pop();
      if (done[u]) continue;
      done[u] = 1;
      for (const [v, k, w] of adj[u]) {
        if (dist[u] + w < dist[v]) { dist[v] = dist[u] + w; prevN[v] = u; prevE[v] = k; h.push(dist[v], v); }
      }
    }
    const base = rnd() * 6.283;
    for (let k = 0; k < nRad; k++) {
      const ang = base + (k / nRad) * 6.283 + (rnd() - 0.5) * 0.5;
      let t = -1, ts = -Infinity;
      out.forEach((q, m) => {
        if (!done[m]) return;
        const r = Math.hypot(q.x - out[c].x, q.y - out[c].y), d = Math.atan2(q.y - out[c].y, q.x - out[c].x) - ang;
        if (Math.cos(d) <= 0) return;
        const sc = r * Math.cos(d) - 2 * r * Math.abs(Math.sin(d));
        if (sc > ts) { ts = sc; t = m; }
      });
      for (let v = t; v >= 0 && prevE[v] >= 0; v = prevN[v]) edges[prevE[v]].main = true;
    }
  }

  // 5. geometry: gentle bends + stats
  let km = 0, mainKm = 0;
  for (const e of edges) {
    const A = out[e.a], B = out[e.b];
    const dx = B.x - A.x, dy = B.y - A.y, chord = Math.hypot(dx, dy) || 1;
    const mx = (A.x + B.x) / 2, my = (A.y + B.y) / 2;
    const bulge = p.curve * chord * 0.25 * (0.6 * noise(mx, my, nE, s * 5) + 0.4 * (rnd() * 2 - 1));
    e.curved = Math.abs(bulge) > 0.5;
    e.cx = mx - (dy / chord) * bulge * 2;
    e.cy = my + (dx / chord) * bulge * 2;
    e.len = e.curved ? (2 * chord + Math.hypot(e.cx - A.x, e.cy - A.y) + Math.hypot(B.x - e.cx, B.y - e.cy)) / 3 : chord;
    km += e.len / 1000;
    if (e.main) mainKm += e.len / 1000;
    A.deg++; B.deg++;
  }
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity, inter = 0, dead = 0;
  for (const q of out) {
    minx = Math.min(minx, q.x); maxx = Math.max(maxx, q.x); miny = Math.min(miny, q.y); maxy = Math.max(maxy, q.y);
    if (q.deg >= 3) inter++;
    if (q.deg === 1) dead++;
  }
  return {
    nodes: out, edges, bounds: { minx, miny, maxx, maxy },
    stats: {
      nodes: n, edges: edges.length, km, mainKm, avgEdge: (km * 1000) / edges.length,
      intersections: inter, deadEnds: dead, density: km / Math.max(areaKm2, 1e-6),
    },
  };
}
