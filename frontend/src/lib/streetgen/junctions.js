// junctions.js: find where a new road meets existing ones, and decide junction / overpass / refuse.
// Junctions that would leave a stub shorter than the min edge are pulled onto the nearby node.

import { LIMITS } from "./params.js";
import { closestOnPolyline, collinearOverlap, cumLengths, segIntersect } from "./geom.js";
import { bridgeHalf, crossingKind } from "./policy.js";

function detect(g, pts, cum) {
  const L = cum[cum.length - 1], hits = [];
  for (let i = 0; i < cum.length - 1; i++) {
    const ax = pts[2 * i], ay = pts[2 * i + 1], bx = pts[2 * i + 2], by = pts[2 * i + 3], len = cum[i + 1] - cum[i];
    for (const eid of g.edgesNearSegment(ax, ay, bx, by, 0)) {
      const e = g.edge(eid);
      for (let j = 0; j < e.cum.length - 1; j++) {
        const cx = e.pts[2 * j], cy = e.pts[2 * j + 1], dx = e.pts[2 * j + 2], dy = e.pts[2 * j + 3];
        if (collinearOverlap(ax, ay, bx, by, cx, cy, dx, dy)) return { error: "it would run on top of an existing road", x: ax, y: ay };
        const h = segIntersect(ax, ay, bx, by, cx, cy, dx, dy);
        if (!h) continue;
        const s = cum[i] + h.t * len;
        if (s < LIMITS.mergeTol || s > L - LIMITS.mergeTol) continue; // the ends are handled by resolveEnds
        hits.push({ s, x: h.x, y: h.y, edgeId: eid, sEdge: e.cum[j] + h.u * (e.cum[j + 1] - e.cum[j]) });
      }
    }
  }
  hits.sort((p, q) => p.s - q.s);
  return hits.filter((h, i) => !hits.slice(0, i).some((p) => p.edgeId === h.edgeId && Math.abs(p.s - h.s) < 0.05));
}

/**
 * Make sure the polyline passes exactly through node n. Interior vertices within a min-edge of n are dropped
 * first, otherwise they would leave slivers (or a stretch lying on the existing road) beside the node.
 * Returns true if the polyline changed.
 */
function ensureVertexAt(P, n) {
  let changed = false;
  for (let i = P.length / 2 - 2; i >= 1; i--) {
    const d = Math.hypot(P[2 * i] - n.x, P[2 * i + 1] - n.y);
    if (d > 1e-9 && d < LIMITS.minEdge) { P.splice(2 * i, 2); changed = true; }
  }
  const cnt = P.length / 2;
  for (let i = 0; i < cnt; i++) {
    const d = Math.hypot(P[2 * i] - n.x, P[2 * i + 1] - n.y);
    if (d < 1e-9) return changed;
    if (d < LIMITS.mergeTol) {
      if (i === 0 || i === cnt - 1) return changed;
      P[2 * i] = n.x; P[2 * i + 1] = n.y;
      return true;
    }
  }
  const c = closestOnPolyline(P, null, n.x, n.y);
  P.splice(2 * (c.seg + 1), 0, n.x, n.y);
  return true;
}

/**
 * Returns { pts, events } or { error, x, y }.
 * events: { kind: "node", nodeId, s } | { kind: "split", edgeId, sEdge, s, x, y }
 *       | { kind: "overpass", over: "new" | "existing", edgeId, sEdge, s, x, y, half }
 */
export function findJunctions(g, road, pts) {
  const P = pts.slice();
  for (let iter = 0; iter < 6; iter++) {
    const hits = detect(g, P, cumLengths(P));
    if (hits.error) return hits;
    const events = [], moves = new Map();
    for (const h of hits) {
      const e = g.edge(h.edgeId), k = crossingKind(road, e);
      if (k.kind === "forbidden") return { error: `it would cross a road here: ${k.why}`, x: h.x, y: h.y };
      if (k.kind === "overpass") {
        events.push({ kind: "overpass", over: k.over, edgeId: h.edgeId, sEdge: h.sEdge, s: h.s, x: h.x, y: h.y, half: bridgeHalf(k.underWidth) });
        continue;
      }
      const nearA = h.sEdge < LIMITS.minEdge, nearB = e.len - h.sEdge < LIMITS.minEdge;
      if (!nearA && !nearB) { events.push({ kind: "split", edgeId: h.edgeId, sEdge: h.sEdge, s: h.s, x: h.x, y: h.y }); continue; }
      const nid = nearA && (!nearB || h.sEdge <= e.len - h.sEdge) ? e.a : e.b;
      if (g.edgesAt(nid).some((o) => crossingKind(road, o).kind !== "junction")) return { error: "a junction here would touch a highway, ramp or bridge", x: h.x, y: h.y };
      const n = g.node(nid);
      moves.set(nid, n);
      if (!events.some((ev) => ev.kind === "node" && ev.nodeId === nid)) events.push({ kind: "node", nodeId: nid, s: h.s, x: n.x, y: n.y });
    }
    let changed = false;
    for (const n of moves.values()) if (ensureVertexAt(P, n)) changed = true;
    if (!changed) return { pts: P, events };
  }
  return { error: "the junctions would not settle on clean positions", x: P[0], y: P[1] };
}
