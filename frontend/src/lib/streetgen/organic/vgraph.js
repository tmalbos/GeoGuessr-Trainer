// organic/vgraph.js: the street graph of the organic style = the edges of the Voronoi diagram of the sites.
// Short edges are merged away, and the downtown cell is collapsed into one square node so that several
// primary roads can meet there (a Voronoi corner only has 3 roads; a square has 5-7).

import { Delaunay } from "d3-delaunay";

function circumcenter(ax, ay, bx, by, cx, cy) {
  const dx = bx - ax, dy = by - ay, ex = cx - ax, ey = cy - ay;
  const bl = dx * dx + dy * dy, cl = ex * ex + ey * ey, d = dx * ey - dy * ex;
  if (Math.abs(d) < 1e-12) return null;
  return [ax + (ey * bl - dy * cl) / (2 * d), ay + (dx * cl - ex * bl) / (2 * d)];
}

/** Returns { verts: [{ x, y, rho }], edges: [[a, b]], hubTris: [vertex ids] } with only vertices inside rho <= maxRho. */
export function voronoiGraph(pts, terrain, maxRho) {
  const del = new Delaunay(pts), tri = del.triangles, he = del.halfedges, nt = tri.length / 3;
  const vid = new Int32Array(nt).fill(-1), verts = [];
  for (let t = 0; t < nt; t++) {
    const a = tri[3 * t], b = tri[3 * t + 1], c = tri[3 * t + 2];
    const cc = circumcenter(pts[2 * a], pts[2 * a + 1], pts[2 * b], pts[2 * b + 1], pts[2 * c], pts[2 * c + 1]);
    if (!cc) continue;
    const rho = terrain.rho(cc[0], cc[1]);
    if (rho <= maxRho) { vid[t] = verts.length; verts.push({ x: cc[0], y: cc[1], rho }); }
  }
  const edges = [];
  for (let e = 0; e < he.length; e++) {
    const o = he[e];
    if (o < e) continue;
    const a = vid[Math.floor(e / 3)], b = vid[Math.floor(o / 3)];
    if (a >= 0 && b >= 0 && a !== b) edges.push([a, b]);
  }
  const hub = [];
  for (let e = 0; e < tri.length; e++) if (tri[e] === 0 && vid[Math.floor(e / 3)] >= 0) hub.push(vid[Math.floor(e / 3)]);
  return { verts, edges, hubTris: [...new Set(hub)] };
}

/** Merge vertices joined by edges shorter than minLen, and merge the downtown cell into one vertex. */
export function contract(g, minLen) {
  const n = g.verts.length, par = Int32Array.from({ length: n }, (_, i) => i), size = new Int32Array(n).fill(1);
  const find = (x) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
  const union = (a, b) => { a = find(a); b = find(b); if (a === b) return; par[b] = a; size[a] += size[b]; };
  for (let i = 1; i < g.hubTris.length; i++) union(g.hubTris[0], g.hubTris[i]);
  const hubRoot = g.hubTris.length ? find(g.hubTris[0]) : -1;
  const len = (e) => Math.hypot(g.verts[e[0]].x - g.verts[e[1]].x, g.verts[e[0]].y - g.verts[e[1]].y);
  for (const e of g.edges.slice().sort((p, q) => len(p) - len(q))) {
    if (len(e) >= minLen) break;
    const a = find(e[0]), b = find(e[1]);
    if (a !== b && size[a] + size[b] <= 4 && a !== hubRoot && b !== hubRoot) union(a, b);
  }
  const id = new Int32Array(n).fill(-1), sum = [], verts = [];
  for (let v = 0; v < n; v++) {
    const r = find(v);
    if (id[r] < 0) { id[r] = verts.length; verts.push({ x: 0, y: 0, rho: 0 }); sum.push(0); }
    const o = verts[id[r]];
    o.x += g.verts[v].x; o.y += g.verts[v].y; o.rho += g.verts[v].rho; sum[id[r]]++;
  }
  verts.forEach((o, i) => { o.x /= sum[i]; o.y /= sum[i]; o.rho /= sum[i]; });
  const seen = new Set(), edges = [];
  for (const [a, b] of g.edges) {
    const x = id[find(a)], y = id[find(b)];
    if (x === y) continue;
    const k = Math.min(x, y) * 4194304 + Math.max(x, y);
    if (seen.has(k)) continue;
    seen.add(k); edges.push([x, y]);
  }
  const adj = verts.map(() => []);
  edges.forEach(([a, b], i) => { adj[a].push({ to: b, edge: i }); adj[b].push({ to: a, edge: i }); });
  return { verts, edges, adj, hub: hubRoot >= 0 ? id[hubRoot] : 0 };
}
