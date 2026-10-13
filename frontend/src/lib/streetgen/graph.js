// graph.js: storage for the road graph (nodes, edges, crossings) and the spatial lookups on it.
// It enforces nothing; the rules live in network.js (planning) and validate.js (checking).

import { cumLengths, closestOnPolyline, closestOnSeg } from "./geom.js";
import { createGrid, createPointGrid } from "./spatialIndex.js";

export function createGraph(bounds) {
  const nodes = new Map(), edges = new Map(), adj = new Map();
  const nodeGrid = createPointGrid(50), edgeGrid = createGrid(100);
  const crossings = [];
  let nextNode = 0, nextEdge = 0;

  const onBorder = (x, y, tol = 1e-6) =>
    x <= bounds.minx + tol || x >= bounds.maxx - tol || y <= bounds.miny + tol || y >= bounds.maxy - tol;

  function addNode(x, y) {
    const id = nextNode++;
    nodes.set(id, { id, x, y, kind: "plain", radius: 0 });
    adj.set(id, new Set());
    nodeGrid.insert(id, x, y);
    return id;
  }

  function addEdge(spec) {
    const a = nodes.get(spec.a), b = nodes.get(spec.b);
    const pts = spec.pts.slice();
    pts[0] = a.x; pts[1] = a.y; pts[pts.length - 2] = b.x; pts[pts.length - 1] = b.y;
    const cum = cumLengths(pts);
    const e = {
      id: nextEdge++, a: spec.a, b: spec.b, cls: spec.cls, highway: !!spec.highway, ramp: !!spec.ramp,
      width: spec.width, layer: spec.layer ?? 0, bridge: !!spec.bridge, pts, cum, len: cum[cum.length - 1],
    };
    edges.set(e.id, e);
    adj.get(e.a).add(e.id);
    adj.get(e.b).add(e.id);
    for (let i = 0; i < pts.length / 2 - 1; i++) edgeGrid.insertSegment(e.id, pts[2 * i], pts[2 * i + 1], pts[2 * i + 2], pts[2 * i + 3]);
    return e.id;
  }

  function removeEdge(id) {
    const e = edges.get(id);
    adj.get(e.a).delete(id);
    adj.get(e.b).delete(id);
    edgeGrid.remove(id);
    edges.delete(id);
  }

  /** Replace one edge by several pieces that keep its class. Crossing records follow the right piece. */
  function replaceEdge(id, pieces) {
    const old = edges.get(id);
    removeEdge(id);
    const ids = pieces.map((p) => addEdge({
      cls: old.cls, highway: old.highway, ramp: old.ramp, width: old.width, layer: old.layer,
      a: p.a, b: p.b, pts: p.pts, bridge: p.bridge || old.bridge, ...(p.bridge ? { layer: 1 } : {}),
    }));
    for (const c of crossings) {
      for (const k of ["overEdge", "underEdge"]) {
        if (c[k] !== id) continue;
        let best = ids[0], bd = Infinity;
        for (const nid of ids) { const e = edges.get(nid); const d = closestOnPolyline(e.pts, e.cum, c.x, c.y).d; if (d < bd) { bd = d; best = nid; } }
        c[k] = best;
      }
    }
    return ids;
  }

  return {
    bounds, crossings, onBorder,
    addNode, addEdge, removeEdge, replaceEdge,
    node: (id) => nodes.get(id),
    edge: (id) => edges.get(id),
    allNodes: () => [...nodes.values()],
    allEdges: () => [...edges.values()],
    edgesAt: (nid) => [...adj.get(nid)].map((id) => edges.get(id)),
    setNodeKind(id, kind, radius = 0) { const n = nodes.get(id); n.kind = kind; n.radius = radius; },
    addCrossing(c) { crossings.push(c); },
    nodesNear: (x, y, r) => nodeGrid.near(x, y, r),
    nearestNode(x, y, r = 1e9) {
      let best = null, bd = r;
      for (const n of nodes.values()) { const d = Math.hypot(n.x - x, n.y - y); if (d < bd) { bd = d; best = n.id; } }
      return best;
    },
    edgesNearPoint: (x, y, r) => [...edgeGrid.queryBox(x - r, y - r, x + r, y + r)],
    edgesNearSegment: (ax, ay, bx, by, pad = 0) => [...edgeGrid.querySegment(ax, ay, bx, by, pad)],
    /** Nodes within r of a segment. */
    nodesNearSegment(ax, ay, bx, by, r) {
      const out = new Set(), n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / 40));
      for (let k = 0; k <= n; k++) {
        const x = ax + ((bx - ax) * k) / n, y = ay + ((by - ay) * k) / n;
        for (const id of nodeGrid.near(x, y, r + 25)) {
          const p = nodes.get(id);
          if (closestOnSeg(p.x, p.y, ax, ay, bx, by).d < r) out.add(id);
        }
      }
      return [...out];
    },
    counts: () => ({ nodes: nodes.size, edges: edges.size, crossings: crossings.length }),
  };
}
