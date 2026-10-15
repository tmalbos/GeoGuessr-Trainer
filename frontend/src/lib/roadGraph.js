// Turns the road layer of a place (see place.js) into a walkable graph of "panorama" nodes,
// the same idea as Street View: you stand on a node and can jump to the nodes linked to it.
// Local coordinates: metres, +x east, +y north.

const STEP_M = 12; // distance between two panoramas along a road

// Numeric hash of a point at 0.5 m resolution (cheaper than string keys on big windows).
const key = (x, y) => (Math.round(x * 2) + 100000) * 400000 + (Math.round(y * 2) + 100000);

export function buildRoadGraph(place, step = STEP_M) {
  const R = place.layers.roads;
  const { start, xy, flag, count } = R;
  const usable = (i) => !(flag[i] & 2) && start[i + 1] - start[i] >= 2; // tunnels are not walkable

  // Pass 1: which vertices are shared by more than one road part? Those are junctions.
  const owner = new Map();
  for (let i = 0; i < count; i++) {
    if (!usable(i)) continue;
    for (let j = start[i]; j < start[i + 1]; j++) {
      const k = key(xy[2 * j], xy[2 * j + 1]);
      const o = owner.get(k);
      if (o === undefined) owner.set(k, i);
      else if (o !== i) owner.set(k, -1);
    }
  }

  const nx = [], ny = [], adj = [];
  const keyed = new Map();
  const addNode = (x, y) => { nx.push(x); ny.push(y); adj.push([]); return nx.length - 1; };
  const keyNode = (x, y) => {
    const k = key(x, y);
    let id = keyed.get(k);
    if (id === undefined) { id = addNode(x, y); keyed.set(k, id); }
    return id;
  };
  const link = (a, b) => {
    if (a === b) return;
    if (!adj[a].includes(b)) adj[a].push(b);
    if (!adj[b].includes(a)) adj[b].push(a);
  };

  // Walk a polyline section and drop evenly spaced nodes on it.
  function section(pts, from, to) {
    const cum = [0];
    for (let i = 1; i < pts.length; i++) cum.push(cum[i - 1] + Math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]));
    const total = cum[cum.length - 1];
    const n = Math.max(1, Math.round(total / step));
    let prev = from, seg = 1;
    for (let k = 1; k < n; k++) {
      const d = (total * k) / n;
      while (seg < pts.length - 1 && cum[seg] < d) seg++;
      const t = (d - cum[seg - 1]) / (cum[seg] - cum[seg - 1] || 1);
      const id = addNode(pts[seg - 1][0] + (pts[seg][0] - pts[seg - 1][0]) * t, pts[seg - 1][1] + (pts[seg][1] - pts[seg - 1][1]) * t);
      link(prev, id);
      prev = id;
    }
    link(prev, to);
  }

  // Pass 2: split each road at its junctions/ends and resample.
  for (let i = 0; i < count; i++) {
    if (!usable(i)) continue;
    const a = start[i], b = start[i + 1];
    let prev = keyNode(xy[2 * a], xy[2 * a + 1]);
    let pts = [[xy[2 * a], xy[2 * a + 1]]];
    for (let j = a + 1; j < b; j++) {
      const x = xy[2 * j], y = xy[2 * j + 1];
      pts.push([x, y]);
      if (j === b - 1 || owner.get(key(x, y)) === -1) {
        const id = keyNode(x, y);
        section(pts, prev, id);
        prev = id;
        pts = [[x, y]];
      }
    }
  }

  return { count: nx.length, x: Float32Array.from(nx), y: Float32Array.from(ny), adj };
}

/** A node near the window centre that sits on a big connected network (not an isolated stub). */
export function pickStart(g) {
  const comp = new Int32Array(g.count).fill(-1);
  const sizes = [];
  for (let s = 0; s < g.count; s++) {
    if (comp[s] !== -1) continue;
    const id = sizes.length;
    let size = 0;
    const stack = [s];
    comp[s] = id;
    while (stack.length) {
      const n = stack.pop();
      size++;
      for (const m of g.adj[n]) if (comp[m] === -1) { comp[m] = id; stack.push(m); }
    }
    sizes.push(size);
  }
  const minSize = Math.max(30, 0.25 * Math.max(...sizes));
  let best = 0, bestD = Infinity;
  for (let n = 0; n < g.count; n++) {
    if (sizes[comp[n]] < minSize) continue;
    const d = g.x[n] * g.x[n] + g.y[n] * g.y[n];
    if (d < bestD) { bestD = d; best = n; }
  }
  return best;
}
