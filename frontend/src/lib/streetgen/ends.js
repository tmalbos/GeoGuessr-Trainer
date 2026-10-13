// ends.js: decide what happens at both ends of a new road: join a road, reach the map edge, or stay open.

import { LIMITS } from "./params.js";
import { closestOnPolyline, dist, segIntersect } from "./geom.js";
import { targetRules } from "./policy.js";

const fail = (error, x, y) => ({ error, x, y });

function snapToBorder(b, x, y) {
  const t = LIMITS.borderTol;
  if (x - b.minx < t) x = b.minx; else if (b.maxx - x < t) x = b.maxx;
  if (y - b.miny < t) y = b.miny; else if (b.maxy - y < t) y = b.maxy;
  return [x, y];
}

function nodeCandidate(g, rules, nid, x, y) {
  if (!rules.node(g.edgesAt(nid))) return null;
  const n = g.node(nid);
  return { kind: "node", nodeId: nid, x: n.x, y: n.y, d: dist(x, y, n.x, n.y) };
}

/** A point on edge e: join the edge there, or, if that would leave a stub shorter than the min edge, join its end node. */
function edgeCandidate(g, rules, role, e, c, x, y) {
  const nearA = c.s < LIMITS.minEdge, nearB = e.len - c.s < LIMITS.minEdge;
  if (!nearA && !nearB) return { kind: "edge", edgeId: e.id, s: c.s, x: c.x, y: c.y, d: c.d };
  if (role === "highway") return null;
  const nid = nearA && (!nearB || c.s <= e.len - c.s) ? e.a : e.b;
  return nodeCandidate(g, rules, nid, x, y);
}

function findTarget(g, rules, role, x, y, tol) {
  let best = null;
  const consider = (c) => {
    if (!c) return;
    if (!best || c.d < best.d - 1e-9 || (Math.abs(c.d - best.d) <= 1e-9 && c.kind === "node" && best.kind !== "node")) best = c;
  };
  for (const nid of g.nodesNear(x, y, tol)) consider(nodeCandidate(g, rules, nid, x, y));
  for (const eid of g.edgesNearPoint(x, y, tol)) {
    const e = g.edge(eid);
    if (!rules.edge(e)) continue;
    const c = closestOnPolyline(e.pts, e.cum, x, y);
    if (c.d > tol) continue;
    const cand = edgeCandidate(g, rules, role, e, c, x, y);
    if (cand && cand.d <= tol + LIMITS.minEdge) consider(cand);
  }
  return best;
}

/** Stretch the end along its own direction until it meets a valid road. */
function findByExtension(g, rules, role, x, y, dx, dy, len) {
  const bx = x + dx * len, by = y + dy * len, hits = [];
  for (const eid of g.edgesNearSegment(x, y, bx, by, 0)) {
    const e = g.edge(eid);
    if (!rules.edge(e)) continue;
    for (let j = 0; j < e.pts.length / 2 - 1; j++) {
      const h = segIntersect(x, y, bx, by, e.pts[2 * j], e.pts[2 * j + 1], e.pts[2 * j + 2], e.pts[2 * j + 3]);
      if (h && h.t > 1e-6) hits.push({ d: h.t * len, e, x: h.x, y: h.y });
    }
  }
  hits.sort((p, q) => p.d - q.d);
  for (const h of hits) {
    const c = closestOnPolyline(h.e.pts, h.e.cum, h.x, h.y);
    const cand = edgeCandidate(g, rules, role, h.e, c, h.x, h.y);
    if (cand) return cand;
  }
  return null;
}

function resolveEnd(g, road, role, x, y, dx, dy, opts) {
  const snapTol = opts.snapTol ?? LIMITS.snapTol, extendLen = opts.extend ?? LIMITS.extend;
  if (g.onBorder(x, y, LIMITS.borderTol)) {
    if (road.cls === "tertiary" && !road.highway) return fail("a tertiary street cannot reach the map edge", x, y);
    if (road.ramp) return fail("a ramp cannot reach the map edge", x, y);
    const [bx, by] = snapToBorder(g.bounds, x, y);
    return { end: { kind: "border", x: bx, y: by } };
  }
  if (road.highway) return fail("a highway must run from map edge to map edge", x, y);
  const rules = targetRules(road, role);
  const t = findTarget(g, rules, role, x, y, snapTol);
  if (t) return { end: t };
  if (extendLen > 0) {
    const e = findByExtension(g, rules, role, x, y, dx, dy, extendLen);
    if (e) return { end: e, extend: true };
  }
  if (road.cls === "tertiary" && !road.ramp && opts.allowOpenEnd !== false) return { end: { kind: "open", x, y } };
  return fail(road.ramp && role === "highway" ? "a ramp must start on a highway" : `a ${road.cls} road must end on a ${road.cls === "primary" ? "primary road" : "primary or secondary road"} or at the map edge`, x, y);
}

/** Returns { pts, ends: [startEnd, endEnd] } or { error, x, y }. Pure: the graph is not touched. */
export function resolveEnds(g, road, pts, opts = {}) {
  const out = pts.slice(), ends = [];
  const roles = road.ramp ? ((opts.highwayEnd ?? 0) === 0 ? ["highway", "street"] : ["street", "highway"]) : ["any", "any"];
  for (const k of [0, 1]) {
    const n = out.length / 2, i = k === 0 ? 0 : n - 1, j = k === 0 ? 1 : n - 2;
    const x = out[2 * i], y = out[2 * i + 1];
    let dx = x - out[2 * j], dy = y - out[2 * j + 1];
    const l = Math.hypot(dx, dy);
    if (l < 1e-9) return fail("the road has no length", x, y);
    dx /= l; dy /= l;
    const r = resolveEnd(g, road, roles[k], x, y, dx, dy, opts);
    if (r.error) return r;
    if (r.extend) { if (k === 0) out.unshift(r.end.x, r.end.y); else out.push(r.end.x, r.end.y); }
    else { out[2 * i] = r.end.x; out[2 * i + 1] = r.end.y; }
    ends.push(r.end);
  }
  return { pts: out, ends };
}
