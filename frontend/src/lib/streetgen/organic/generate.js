// organic/generate.js: the "organic" city (messy, medieval-like). Ideas from watabou's TownGeneratorOS.
//
//  1. Block centres -> Voronoi diagram. Its edges are the *candidate* streets; the downtown cell becomes a square.
//  2. Primary roads: A* from gates on the rim to the square, smoothed, continued to the map border.
//  3. Secondary roads: from a point far from any main road, search outward to TWO main roads, so both ends
//     are closed by construction (R3).
//  4. Tertiary roads: all remaining candidate edges, thinned toward the rim, plus cul-de-sac stubs.
// Every road goes through net.tryAddRoad, which enforces the rules; this file only proposes roads.

import { chaikin, closestOnPolyline, simplifyDP } from "../geom.js";
import { createPointGrid } from "../spatialIndex.js";
import { defaultBlock } from "../params.js";
import { contract, voronoiGraph } from "./vgraph.js";
import { flood, route } from "./search.js";
import { relax, scatterSites } from "./sites.js";

const TAU = Math.PI * 2;
const angDiff = (a, b) => { const d = Math.abs(a - b) % TAU; return d > Math.PI ? TAU - d : d; };
const flat = (list) => list.flatMap((p) => p);

function sample(pts, step, fn) {
  for (let i = 0; i < pts.length / 2 - 1; i++) {
    const ax = pts[2 * i], ay = pts[2 * i + 1], bx = pts[2 * i + 2], by = pts[2 * i + 3];
    const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / step));
    for (let k = 0; k <= n; k++) fn(ax + ((bx - ax) * k) / n, ay + ((by - ay) * k) / n);
  }
}

export function generateOrganic({ P, rng, terrain, net }) {
  const r = rng.fork("organic"), irr = P.irregularity, H = terrain.H, [cx, cy] = terrain.center;
  const block = P.blockSize ?? defaultBlock("organic");
  const info = { primaries: 0, secondaries: 0, tertiaries: 0, stubs: 0 };

  // 1. candidate streets
  const sites = scatterSites(terrain, rng, block);
  const pts = relax(sites.pts, sites.box, Math.round(3 * (1 - irr)));
  const G = contract(voronoiGraph(pts, terrain, 0.97), Math.max(36, 0.5 * block));
  const V = G.verts, nv = V.length, used = new Set();
  const edgeBetween = (a, b) => G.adj[a].find((x) => x.to === b)?.edge;
  const markUsed = (path) => { for (let i = 0; i < path.length - 1; i++) { const e = edgeBetween(path[i], path[i + 1]); if (e !== undefined) used.add(e); } };
  const toPts = (path) => path.map((v) => [V[v].x, V[v].y]);

  // "near a main road" bookkeeping
  const vgrid = createPointGrid(40), mainGrid = createPointGrid(30), nearMain = new Uint8Array(nv);
  V.forEach((v, i) => vgrid.insert(i, v.x, v.y));
  let sid = 0;
  const markMain = (poly) => sample(poly, 15, (x, y) => { mainGrid.insert(sid++, x, y); for (const v of vgrid.near(x, y, 20)) nearMain[v] = 1; });

  // 2. primary roads
  const nGates = P.sizeKm <= 5 ? 5 : P.sizeKm <= 10 ? 6 : 7;
  const base = r.range(0, TAU), taken = new Set(), gates = [];
  for (let i = 0; i < nGates; i++) {
    const th = base + (i * TAU) / nGates + r.range(-0.5, 0.5) * (TAU / nGates) * (0.3 + 0.4 * irr);
    let best = -1, bs = Infinity;
    for (let v = 0; v < nv; v++) {
      if (taken.has(v) || V[v].rho < 0.72) continue;
      const s = angDiff(th, Math.atan2(V[v].y - cy, V[v].x - cx)) * terrain.R0 + Math.abs(V[v].rho - 0.9) * 0.5 * terrain.R0;
      if (s < bs) { bs = s; best = v; }
    }
    if (best >= 0) { taken.add(best); gates.push({ v: best, th: Math.atan2(V[best].y - cy, V[best].x - cx) }); }
  }
  const rayToBorder = (x, y, dx, dy) => {
    let t = 1e9;
    if (dx > 1e-9) t = Math.min(t, (H - x) / dx); else if (dx < -1e-9) t = Math.min(t, (-H - x) / dx);
    if (dy > 1e-9) t = Math.min(t, (H - y) / dy); else if (dy < -1e-9) t = Math.min(t, (-H - y) / dy);
    return t;
  };
  /** Points from the gate out to the map border, gently wandering. */
  const exitRoad = (v) => {
    const p = V[v];
    let dx = p.x - cx, dy = p.y - cy; const l = Math.hypot(dx, dy) || 1; dx /= l; dy /= l;
    const t = rayToBorder(p.x, p.y, dx, dy), n = Math.max(2, Math.ceil(t / 150));
    const amp = Math.min(70, 0.05 * t) * (0.3 + irr), ph = r.range(0, TAU);
    const out = [];
    for (let k = 1; k <= n; k++) {
      const s = k / n, off = amp * Math.sin(1.5 * Math.PI * s + ph) * (1 - s * s);
      out.push([p.x + dx * t * s - dy * off, p.y + dy * t * s + dx * off]);
    }
    return out;
  };
  const finish = (poly, rounds, tol) => simplifyDP(chaikin(flat(poly), rounds), tol);
  const addPrimary = (poly, paths) => {
    const res = net.tryAddRoad(poly, "primary", { snapTol: 32 });
    if (res.ok) { for (const p of paths) markUsed(p); markMain(poly); info.primaries++; }
    return res.ok;
  };

  let first = false;
  const order = gates.map((g, i) => i).sort(() => r.next() - 0.5);
  for (const i of order) {
    if (first) break;
    const a = gates[i];
    const opp = gates.filter((g) => g !== a).sort((p, q) => Math.abs(angDiff(p.th, a.th) - Math.PI) - Math.abs(angDiff(q.th, a.th) - Math.PI))[0];
    if (!opp) continue;
    const pa = route(G, a.v, G.hub), pb = route(G, opp.v, G.hub);
    if (!pa || !pb) continue;
    const poly = finish([...exitRoad(a.v).reverse(), ...toPts(pa), ...toPts(pb).reverse().slice(1), ...exitRoad(opp.v)], 2, 2.5);
    if (addPrimary(poly, [pa, pb])) { first = true; a.done = opp.done = true; }
  }
  if (!first) { // fall back to a plain road through downtown so the rest of the city has a skeleton
    const th = r.range(0, TAU), dx = Math.cos(th), dy = Math.sin(th);
    const t1 = rayToBorder(cx, cy, dx, dy), t2 = rayToBorder(cx, cy, -dx, -dy);
    if (net.tryAddRoad([cx - dx * t2, cy - dy * t2, cx + dx * t1, cy + dy * t1], "primary").ok) { first = true; info.primaries++; markMain([cx - dx * t2, cy - dy * t2, cx + dx * t1, cy + dy * t1]); }
  }
  for (const g of gates) {
    if (g.done) continue;
    const path = route(G, g.v, G.hub, { used, usedPenalty: 8 });
    if (!path) continue;
    addPrimary(finish([...exitRoad(g.v).reverse(), ...toPts(path)], 2, 2.5), [path]);
  }

  // 3. secondary roads: link two main roads through the least served part of the city
  const covR = Math.max(260, 4.2 * block), tried = new Set();
  const inner = [];
  for (let v = 0; v < nv; v++) if (V[v].rho < 0.9) inner.push(v);
  const chord = (a, b) => Math.hypot(V[a].x - V[b].x, V[a].y - V[b].y);
  const plen = (path) => { let s = 0; for (let i = 0; i < path.length - 1; i++) s += chord(path[i], path[i + 1]); return s; };
  for (let iter = 0, fails = 0; iter < 160 && fails < 50 && inner.length; iter++) {
    let v = -1;
    for (let k = 0; k < 60; k++) {
      const c = inner[r.int(0, inner.length - 1)];
      if (tried.has(c) || nearMain[c] || mainGrid.near(V[c].x, V[c].y, covR).length) continue;
      v = c; break;
    }
    if (v < 0) { fails++; continue; }
    tried.add(v);
    const f1 = flood(G, v, { isGoal: (u) => nearMain[u] === 1, maxCost: 2400 });
    if (!f1.goals.length) continue;
    const p1 = f1.pathTo(f1.goals[0]);
    const f2 = flood(G, v, { isGoal: (u) => nearMain[u] === 1, maxCost: 2400, blocked: new Set(p1.slice(1)) });
    const g2 = f2.goals.find((u) => chord(u, f1.goals[0]) >= 260);
    if (g2 === undefined) continue;
    const p2 = f2.pathTo(g2);
    if (plen(p1) + plen(p2) < 5 * block) continue;
    const poly = finish([...toPts(p1).reverse(), ...toPts(p2).slice(1)], 1, 2);
    if (net.tryAddRoad(poly, "secondary", { snapTol: 32 }).ok) { markUsed(p1); markUsed(p2); markMain(poly); info.secondaries++; }
  }

  // 4. tertiary roads: everything else, thinned toward the rim; sweeps let each street attach to what exists
  const depth = new Array(nv).fill(Infinity), queue = [];
  for (let v = 0; v < nv; v++) if (nearMain[v]) { depth[v] = 0; queue.push(v); }
  for (let h = 0; h < queue.length; h++) for (const { to } of G.adj[queue[h]]) if (depth[to] === Infinity) { depth[to] = depth[queue[h]] + 1; queue.push(to); }
  let pending = [];
  G.edges.forEach(([a, b], i) => {
    if (used.has(i) || V[a].rho > 0.93 || V[b].rho > 0.93) return;
    const rho = (V[a].rho + V[b].rho) / 2;
    if (r.next() < Math.min(0.7, Math.max(0, (rho - 0.45) * 1.3)) * (0.6 + 0.8 * irr)) return;
    const mx = (V[a].x + V[b].x) / 2, my = (V[a].y + V[b].y) / 2, l = chord(a, b) || 1;
    const bend = r.range(-1, 1) * (0.03 + 0.1 * irr) * l;
    pending.push({ d: Math.min(depth[a], depth[b]) + r.next() * 0.5, poly: [V[a].x, V[a].y, mx - ((V[b].y - V[a].y) / l) * bend, my + ((V[b].x - V[a].x) / l) * bend, V[b].x, V[b].y] });
  });
  pending.sort((p, q) => p.d - q.d);
  for (let sweep = 0; sweep < 10 && pending.length; sweep++) {
    const next = []; let added = 0;
    for (const c of pending) {
      const res = net.tryAddRoad(c.poly, "tertiary", { within: terrain.polygon });
      if (res.ok) added++; else if (res.error.includes("cut off")) next.push(c);
    }
    info.tertiaries += added;
    pending = next;
    if (!added) break;
  }

  // cul-de-sacs: short dead-end stubs into the big blocks near the rim
  const g = net.graph, pStub = 0.3 + 0.3 * irr;
  for (let i = 1; i < pts.length / 2; i++) {
    const x = pts[2 * i], y = pts[2 * i + 1];
    if (terrain.rho(x, y) > 0.85 || r.next() > pStub) continue;
    let best = null;
    for (const eid of g.edgesNearPoint(x, y, 160)) { const e = g.edge(eid), c = closestOnPolyline(e.pts, e.cum, x, y); if (!best || c.d < best.d) best = c; }
    if (!best || best.d < 80) continue;
    const len = Math.min(0.62 * best.d, 90), dx = (x - best.x) / best.d, dy = (y - best.y) / best.d;
    if (len >= 32 && net.tryAddRoad([best.x, best.y, best.x + dx * len, best.y + dy * len], "tertiary", { within: terrain.polygon }).ok) info.stubs++;
  }
  return info;
}
