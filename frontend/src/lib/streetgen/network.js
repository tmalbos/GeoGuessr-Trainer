// network.js: the one door into the road graph.
//
//   tryAddRoad(pts, cls, opts) -> { ok: true, edgeIds, nodeIds } | { ok: false, error, x, y }
//
// A road is checked completely first (plan) and only then written (commit), so a refused road leaves the
// graph exactly as it was. Roads already in the graph keep their class forever; they may only be cut into
// pieces at junctions, or get a bridge section where something crosses them.

import { CLS_RANK, LIMITS, midWidth } from "./params.js";
import {
  angleDeg, closestOnPolyline, cumLengths, pointAt, pointInPolygon, polylineSelfIntersection, segIntersect, tangentAt,
} from "./geom.js";
import { createGraph } from "./graph.js";
import { resolveEnds } from "./ends.js";
import { findJunctions } from "./junctions.js";
import { subdivide } from "./pieces.js";
import { clipPolylineToRect } from "./geom.js";

const fail = (error, x = 0, y = 0) => ({ ok: false, error, x, y });

function cleanPts(pts) {
  const out = [pts[0], pts[1]], n = pts.length / 2;
  for (let i = 1; i < n; i++) {
    const x = pts[2 * i], y = pts[2 * i + 1];
    if (Math.hypot(x - out[out.length - 2], y - out[out.length - 1]) < 0.01) {
      if (i === n - 1) { out[out.length - 2] = x; out[out.length - 1] = y; }
      continue;
    }
    out.push(x, y);
  }
  return out;
}

function makeRoad(cls, opts) {
  const highway = !!opts.highway, ramp = !!opts.ramp;
  if (!(cls in CLS_RANK)) return { error: `unknown class "${cls}"` };
  if (highway && (cls !== "primary" || ramp)) return { error: "a highway is a primary road with the highway flag" };
  if (ramp && cls !== "secondary") return { error: "a ramp is a secondary road with the ramp flag" };
  return { cls, highway, ramp, width: opts.width ?? midWidth(highway ? "highway" : cls) };
}

// ── planning ─────────────────────────────────────────────────────────────────

function plan(g, road, rawPts, opts, rails, streets) {
  if (!rawPts || rawPts.length < 4 || rawPts.some((v) => !Number.isFinite(v))) return fail("a road needs at least two valid points");
  const b = g.bounds;
  let pts = cleanPts(rawPts);
  if (pts.length < 4) return fail("a road needs at least two distinct points");
  const runs = clipPolylineToRect(pts, b.minx, b.miny, b.maxx, b.maxy);
  if (runs.length === 0) return fail("the road is outside the map", pts[0], pts[1]);
  if (runs.length > 1) return fail("the road leaves the map and comes back", pts[0], pts[1]);
  pts = cleanPts(runs[0]);
  if (pts.length < 4) return fail("the road has no length inside the map", pts[0], pts[1]);

  const r1 = resolveEnds(g, road, pts, opts);
  if (r1.error) return fail(r1.error, r1.x, r1.y);
  const r2 = findJunctions(g, road, cleanPts(r1.pts));
  if (r2.error) return fail(r2.error, r2.x, r2.y);
  const P = r2.pts, ends = r1.ends, events = r2.events;

  const self = polylineSelfIntersection(P);
  if (self) return fail("the road would cross itself", self.x, self.y);
  for (const rail of rails) for (let i = 0; i < P.length / 2 - 1; i++) for (let j = 0; j < rail.pts.length / 2 - 1; j++) {
    const h = segIntersect(P[2 * i], P[2 * i + 1], P[2 * i + 2], P[2 * i + 3], rail.pts[2 * j], rail.pts[2 * j + 1], rail.pts[2 * j + 2], rail.pts[2 * j + 3]);
    if (h) return fail("the road would cross the railway at ground level", h.x, h.y);
  }

  if (opts.within) {
    for (let i = 0; i < P.length; i += 2) if (!pointInPolygon(P[i], P[i + 1], opts.within)) return fail("the road would leave the allowed area", P[i], P[i + 1]);
  }
  // A road must join the network (R6). Highways are exempt (ramps join them later) and so is the very first road.
  const joins = ends.some((e) => e.kind === "node" || e.kind === "edge") || events.some((e) => e.kind === "node" || e.kind === "split");
  if (!joins && !road.highway && streets > 0 && !opts.allowIsland) return fail("the road would be cut off from the rest of the network", P[0], P[1]);

  const cum = cumLengths(P), L = cum[cum.length - 1];
  const pMarks = [], eMarks = new Map(), crossSpecs = [];
  let spanSeq = 0;
  const marksOf = (e) => {
    let m = eMarks.get(e.id);
    if (!m) eMarks.set(e.id, (m = [
      { s: 0, x: e.pts[0], y: e.pts[1], ref: { id: e.a } },
      { s: e.len, x: e.pts[e.pts.length - 2], y: e.pts[e.pts.length - 1], ref: { id: e.b } },
    ]));
    return m;
  };

  [[ends[0], 0], [ends[1], L]].forEach(([info, s]) => {
    let ref;
    if (info.kind === "node") ref = { id: info.nodeId };
    else {
      ref = { x: info.x, y: info.y };
      if (info.kind === "edge") { const e = g.edge(info.edgeId); const c = closestOnPolyline(e.pts, e.cum, info.x, info.y); marksOf(e).push({ s: c.s, x: c.x, y: c.y, ref }); }
    }
    pMarks.push({ s, x: info.x, y: info.y, ref });
  });

  for (const ev of events) {
    const e = g.edge(ev.edgeId);
    if (ev.kind === "node") pMarks.push({ s: ev.s, x: ev.x, y: ev.y, ref: { id: ev.nodeId } });
    else if (ev.kind === "split") {
      const ref = { x: ev.x, y: ev.y };
      pMarks.push({ s: ev.s, x: ev.x, y: ev.y, ref });
      marksOf(e).push({ s: ev.sEdge, x: ev.x, y: ev.y, ref });
    } else {
      const id = ++spanSeq;
      const [pts2, cum2, c, sC, marks] = ev.over === "new" ? [P, cum, cum[cum.length - 1], ev.s, pMarks] : [e.pts, e.cum, e.len, ev.sEdge, marksOf(e)];
      const s0 = sC - ev.half, s1 = sC + ev.half;
      if (s0 < 0 || s1 > c) return fail("a bridge here would run off the end of the road", ev.x, ev.y);
      const p0 = pointAt(pts2, cum2, s0), p1 = pointAt(pts2, cum2, s1);
      marks.push({ s: s0, x: p0.x, y: p0.y, ref: { x: p0.x, y: p0.y }, span: id }, { s: s1, x: p1.x, y: p1.y, ref: { x: p1.x, y: p1.y }, spanEnd: id });
      crossSpecs.push(ev.over === "new"
        ? { x: ev.x, y: ev.y, over: { owner: "new", s: ev.s }, under: { owner: ev.edgeId, s: ev.sEdge } }
        : { x: ev.x, y: ev.y, over: { owner: ev.edgeId, s: ev.sEdge }, under: { owner: "new", s: ev.s } });
    }
  }

  const cut = subdivide(P, cum, pMarks);
  if (cut.error) return fail(cut.error, cut.x, cut.y);
  const replaced = new Map();
  for (const [eid, marks] of eMarks) {
    const e = g.edge(eid), r = subdivide(e.pts, e.cum, marks);
    if (r.error) return fail(r.error, r.x, r.y);
    replaced.set(eid, r.pieces);
  }

  const bad = checkPlan(g, road, P, ends, cut.pieces, replaced, pMarks);
  if (bad) return fail(bad.error, bad.x, bad.y);
  return { ok: true, road, pieces: cut.pieces, replaced, crossSpecs };
}

function checkPlan(g, road, P, ends, pieces, replaced, pMarks) {
  // refs -> stable keys
  const uid = new Map();
  const keyOf = (ref) => (ref.id !== undefined ? `n${ref.id}` : uid.get(ref) ?? (uid.set(ref, `new${uid.size}`), uid.get(ref)));
  const all = [...pieces, ...[...replaced.values()].flat()];

  for (const p of pieces) if (keyOf(p.a) === keyOf(p.b)) return { error: "the road would loop back onto the same junction", x: p.pts[0], y: p.pts[1] };
  for (const p of pieces) {
    if (p.a.id === undefined || p.b.id === undefined) continue;
    for (const o of g.edgesAt(p.a.id)) {
      if (replaced.has(o.id) || (o.a !== p.b.id && o.b !== p.b.id)) continue;
      const mid = pointAt(p.pts, cumLengths(p.pts), cumLengths(p.pts).at(-1) / 2);
      if (closestOnPolyline(o.pts, o.cum, mid.x, mid.y).d < 5) return { error: "it would duplicate an existing road", x: mid.x, y: mid.y };
    }
  }

  // junction angles at every node the plan touches
  const tang = new Map(), at = new Map();
  const push = (ref, pts, atStart) => {
    const k = keyOf(ref);
    if (!tang.has(k)) tang.set(k, []);
    tang.get(k).push(tangentAt(pts, atStart));
    at.set(k, ref.id !== undefined ? [g.node(ref.id).x, g.node(ref.id).y] : [ref.x, ref.y]);
  };
  for (const p of all) { push(p.a, p.pts, true); push(p.b, p.pts, false); }
  for (const [k, list] of tang) {
    if (!/^n\d+$/.test(k)) continue;
    const id = Number(k.slice(1));
    for (const e of g.edgesAt(id)) if (!replaced.has(e.id)) list.push(tangentAt(e.pts, e.a === id));
  }
  for (const [k, list] of tang) {
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      if (angleDeg(list[i][0], list[i][1], list[j][0], list[j][1]) < LIMITS.minAngle) return { error: `two roads would meet at less than ${LIMITS.minAngle}°`, x: at.get(k)[0], y: at.get(k)[1] };
    }
  }

  // free ends must not almost touch something
  const touched = new Set(pMarks.filter((m) => m.ref.id !== undefined).map((m) => m.ref.id));
  const cum = cumLengths(P), L = cum.at(-1);
  for (const [k, info] of ends.entries()) {
    if (info.kind !== "open") continue;
    for (const eid of g.edgesNearPoint(info.x, info.y, LIMITS.nearMiss)) {
      const e = g.edge(eid);
      if (closestOnPolyline(e.pts, e.cum, info.x, info.y).d < LIMITS.nearMiss) return { error: `the free end would stop less than ${LIMITS.nearMiss} m from another road without joining it`, x: info.x, y: info.y };
    }
    const sEnd = k === 0 ? 0 : L;
    for (let i = 0; i < cum.length - 1; i++) {
      if (Math.min(Math.abs(cum[i] - sEnd), Math.abs(cum[i + 1] - sEnd)) < 40) continue;
      const d = closestOnPolyline(P.slice(2 * i, 2 * i + 4), [0, cum[i + 1] - cum[i]], info.x, info.y).d;
      if (d < LIMITS.nearMiss) return { error: "the free end would curl back next to the road itself", x: info.x, y: info.y };
    }
  }
  for (let i = 0; i < cum.length - 1; i++) {
    for (const nid of g.nodesNearSegment(P[2 * i], P[2 * i + 1], P[2 * i + 2], P[2 * i + 3], LIMITS.nearMiss)) {
      if (touched.has(nid)) continue;
      const es = g.edgesAt(nid), n = g.node(nid);
      if (es.length === 1 && !g.onBorder(n.x, n.y)) return { error: "it would pass right next to a dead end without joining it", x: n.x, y: n.y };
    }
  }
  return null;
}

// ── commit ───────────────────────────────────────────────────────────────────

function commit(g, p) {
  const made = new Map();
  const nodeOf = (ref) => {
    if (ref.id !== undefined) return ref.id;
    if (!made.has(ref)) made.set(ref, g.addNode(ref.x, ref.y));
    return made.get(ref);
  };
  const owned = new Map(); // owner -> [{ id, s0, s1, bridge }]
  for (const [eid, pcs] of p.replaced) {
    const ids = g.replaceEdge(eid, pcs.map((q) => ({ a: nodeOf(q.a), b: nodeOf(q.b), pts: q.pts, bridge: q.bridge })));
    owned.set(eid, pcs.map((q, i) => ({ id: ids[i], s0: q.s0, s1: q.s1, bridge: q.bridge })));
  }
  const edgeIds = [];
  owned.set("new", p.pieces.map((q) => {
    const id = g.addEdge({ a: nodeOf(q.a), b: nodeOf(q.b), cls: p.road.cls, highway: p.road.highway, ramp: p.road.ramp, width: p.road.width, pts: q.pts, bridge: q.bridge, layer: q.bridge ? 1 : 0 });
    edgeIds.push(id);
    return { id, s0: q.s0, s1: q.s1, bridge: q.bridge };
  }));
  const find = (ref, wantBridge) => {
    const list = owned.get(ref.owner);
    if (!list) return ref.owner;
    const hit = list.find((q) => ref.s >= q.s0 - 1e-6 && ref.s <= q.s1 + 1e-6 && (!wantBridge || q.bridge));
    return (hit || list[0]).id;
  };
  for (const c of p.crossSpecs) g.addCrossing({ x: c.x, y: c.y, kind: "overpass", overEdge: find(c.over, true), underEdge: find(c.under, false) });
  return { ok: true, edgeIds, nodeIds: [...made.values()] };
}

// ── rail (basic: laid on top of the roads, crossing them on bridges) ─────────

function planRail(g, rawPts, width, rails) {
  const b = g.bounds;
  let pts = cleanPts(rawPts);
  const runs = clipPolylineToRect(pts, b.minx, b.miny, b.maxx, b.maxy);
  if (runs.length !== 1) return fail("the railway must stay inside the map in one piece");
  pts = cleanPts(runs[0]);
  const ends = [0, pts.length - 2].map((i) => [pts[i], pts[i + 1]]);
  if (!ends.every(([x, y]) => g.onBorder(x, y, LIMITS.borderTol))) return fail("a railway must run from map edge to map edge", ends[0][0], ends[0][1]);
  for (const k of [0, 1]) {
    const i = k === 0 ? 0 : pts.length - 2;
    const t = LIMITS.borderTol;
    if (pts[i] - b.minx < t) pts[i] = b.minx; else if (b.maxx - pts[i] < t) pts[i] = b.maxx;
    if (pts[i + 1] - b.miny < t) pts[i + 1] = b.miny; else if (b.maxy - pts[i + 1] < t) pts[i + 1] = b.maxy;
  }
  const self = polylineSelfIntersection(pts);
  if (self) return fail("the railway would cross itself", self.x, self.y);
  const crossings = [];
  for (let i = 0; i < pts.length / 2 - 1; i++) {
    const [ax, ay, bx, by] = [pts[2 * i], pts[2 * i + 1], pts[2 * i + 2], pts[2 * i + 3]];
    for (const nid of g.nodesNearSegment(ax, ay, bx, by, width / 2 + 4)) { const n = g.node(nid); return fail("the railway would pass through a junction", n.x, n.y); }
    for (const eid of g.edgesNearSegment(ax, ay, bx, by, 0)) {
      const e = g.edge(eid);
      for (let j = 0; j < e.cum.length - 1; j++) {
        const h = segIntersect(ax, ay, bx, by, e.pts[2 * j], e.pts[2 * j + 1], e.pts[2 * j + 2], e.pts[2 * j + 3]);
        if (!h) continue;
        if (e.layer !== 0 || e.bridge || e.ramp) return fail("the railway would cross a bridge or ramp", h.x, h.y);
        if (!crossings.some((c) => c.underEdge === eid && Math.hypot(c.x - h.x, c.y - h.y) < 1)) crossings.push({ x: h.x, y: h.y, underEdge: eid });
      }
    }
    for (const r of rails) for (let j = 0; j < r.pts.length / 2 - 1; j++) {
      const h = segIntersect(ax, ay, bx, by, r.pts[2 * j], r.pts[2 * j + 1], r.pts[2 * j + 2], r.pts[2 * j + 3]);
      if (h) return fail("railways cannot cross each other", h.x, h.y);
    }
  }
  return { ok: true, pts, crossings };
}

// ── public ───────────────────────────────────────────────────────────────────

export function createNetwork(bounds) {
  const g = createGraph(bounds);
  const rails = [];
  let streets = 0; // non-highway roads committed so far
  return {
    graph: g,
    rails,
    tryAddRoad(pts, cls, opts = {}) {
      const road = makeRoad(cls, opts);
      if (road.error) return fail(road.error);
      const p = plan(g, road, pts, opts, rails, streets);
      if (!p.ok) return p;
      if (!road.highway) streets++;
      return commit(g, p);
    },
    addRail(pts, width = midWidth("rail")) {
      const p = planRail(g, pts, width, rails);
      if (!p.ok) return p;
      const rail = { id: rails.length, pts: p.pts, width };
      rails.push(rail);
      for (const c of p.crossings) g.addCrossing({ x: c.x, y: c.y, kind: "rail_over", rail: rail.id, underEdge: c.underEdge });
      return { ok: true, id: rail.id };
    },
  };
}
