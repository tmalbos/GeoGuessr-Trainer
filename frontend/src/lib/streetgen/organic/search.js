// organic/search.js: path finding on the street graph.
//  - route(): A* with a turn penalty, for primary roads (long, direct, few bends)
//  - flood(): Dijkstra that reports every "goal" vertex it reaches, for secondary roads

class Heap {
  constructor() { this.a = []; }
  get size() { return this.a.length; }
  push(k, v) {
    const a = this.a; a.push([k, v]);
    let i = a.length - 1;
    while (i > 0) { const p = (i - 1) >> 1; if (a[p][0] <= a[i][0]) break; [a[p], a[i]] = [a[i], a[p]]; i = p; }
  }
  pop() {
    const a = this.a, top = a[0], last = a.pop();
    if (a.length) {
      a[0] = last;
      let i = 0;
      for (;;) {
        const l = 2 * i + 1, r = l + 1; let m = i;
        if (l < a.length && a[l][0] < a[m][0]) m = l;
        if (r < a.length && a[r][0] < a[m][0]) m = r;
        if (m === i) break;
        [a[m], a[i]] = [a[i], a[m]]; i = m;
      }
    }
    return top;
  }
}

const elen = (G, e) => { const [a, b] = G.edges[e]; return Math.hypot(G.verts[a].x - G.verts[b].x, G.verts[a].y - G.verts[b].y); };

/** A* over directed edges. Returns the list of vertex ids from start to goal, or null. */
export function route(G, start, goal, { turnWeight = 1.2, used = null, usedPenalty = 6, blocked = null } = {}) {
  const V = G.verts, gv = V[goal];
  const heap = new Heap(), best = new Map(), from = new Map();
  const dirOf = (e, fwd) => { const [a, b] = G.edges[e], p = V[fwd ? a : b], q = V[fwd ? b : a]; const l = Math.hypot(q.x - p.x, q.y - p.y) || 1; return [(q.x - p.x) / l, (q.y - p.y) / l]; };
  const h = (v) => Math.hypot(V[v].x - gv.x, V[v].y - gv.y);
  for (const { to, edge } of G.adj[start]) {
    if (blocked && blocked.has(to) && to !== goal) continue;
    const fwd = G.edges[edge][0] === start, sid = edge * 2 + (fwd ? 0 : 1);
    const c = elen(G, edge) * (used && used.has(edge) ? usedPenalty : 1);
    best.set(sid, c); from.set(sid, -1); heap.push(c + h(to), sid);
  }
  while (heap.size) {
    const [, sid] = heap.pop(), e = sid >> 1, fwd = (sid & 1) === 0, head = G.edges[e][fwd ? 1 : 0];
    if (head === goal) {
      const path = [goal];
      for (let s = sid; s !== -1; s = from.get(s)) path.push(G.edges[s >> 1][(s & 1) === 0 ? 0 : 1]);
      return path.reverse();
    }
    const g0 = best.get(sid), d0 = dirOf(e, fwd);
    for (const { to, edge } of G.adj[head]) {
      if (edge === e || (blocked && blocked.has(to) && to !== goal)) continue;
      const f2 = G.edges[edge][0] === head, sid2 = edge * 2 + (f2 ? 0 : 1), d1 = dirOf(edge, f2);
      const ang = Math.acos(Math.max(-1, Math.min(1, d0[0] * d1[0] + d0[1] * d1[1])));
      const c = g0 + elen(G, edge) * (1 + turnWeight * (ang / (Math.PI / 2)) ** 2) * (used && used.has(edge) ? usedPenalty : 1);
      if (c < (best.get(sid2) ?? Infinity)) { best.set(sid2, c); from.set(sid2, sid); heap.push(c + h(to), sid2); }
    }
  }
  return null;
}

/**
 * Dijkstra from `start`. Vertices where isGoal() is true are reported (nearest first) and not expanded.
 * Returns { goals: [vertex ids], pathTo(v) }.
 */
export function flood(G, start, { isGoal, blocked = null, maxCost = Infinity }) {
  const dist = new Map([[start, 0]]), prev = new Map(), heap = new Heap(), goals = [], done = new Set();
  heap.push(0, start);
  while (heap.size) {
    const [d, v] = heap.pop();
    if (done.has(v)) continue;
    done.add(v);
    if (v !== start && isGoal(v)) { goals.push(v); continue; }
    for (const { to, edge } of G.adj[v]) {
      if (blocked && blocked.has(to)) continue;
      const d2 = d + elen(G, edge);
      if (d2 <= maxCost && d2 < (dist.get(to) ?? Infinity)) { dist.set(to, d2); prev.set(to, v); heap.push(d2, to); }
    }
  }
  return {
    goals,
    cost: (v) => dist.get(v),
    pathTo(v) { const p = [v]; while (prev.has(p[p.length - 1])) p.push(prev.get(p[p.length - 1])); return p.reverse(); },
  };
}
