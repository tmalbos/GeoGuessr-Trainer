// validate.js: checks a finished City against the rules R1-R14 of the brief. Independent of how the city was built.
// Returns a list of { rule, message, x, y, severity: "error" | "warn", nodeId?, edgeId? }.
// "warn" is for tunable targets (R13, ramp/bridge spacing); everything else is an error.

import { LIMITS } from "./params.js";
import { createGrid } from "./spatialIndex.js";
import {
  angleDeg, closestOnPolyline, closestOnSeg, collinearOverlap, cumLengths, pointInPolygon, polylineSelfIntersection,
  segIntersect, tangentAt,
} from "./geom.js";

export const RULE_TEXT = {
  R1: "Dead ends are tertiary only",
  R2: "Primary roads end on a primary road or the map edge",
  R3: "Secondary roads end on a primary or secondary road or the map edge",
  R4: "Highways run edge to edge and meet other roads only at ramps",
  R5: "Ramps join a highway to a primary or secondary road; highways have ramps",
  R6: "One connected network",
  R7: "Every junction is a node; crossings are bridges or overpasses",
  R8: "Roundabouts: 5+ roads, none tertiary, never on highways or ramps",
  R9: "Signals: 3+ roads, at least two non-tertiary, never on highways or ramps",
  R10: "No roads in water except bridges over rivers",
  R11: "Rail runs edge to edge, never through a junction, crosses roads on bridges",
  R12: "Sane geometry: no tiny edges, sharp angles, loops or duplicates",
  R13: "Long roads: length targets per class",
  R14: "Only highways, primary and secondary roads reach the map edge",
  GEN: "Generator",
};

const SEG = 100000;
const pairKey = (a, b) => (a < b ? `${a}-${b}` : `${b}-${a}`);
const isStreetPrimary = (e) => e.cls === "primary" && !e.highway;

function buildCtx(city) {
  const nodes = new Map(city.nodes.map((n) => [n.id, n]));
  const edges = new Map(city.edges.map((e) => [e.id, e]));
  const at = new Map(city.nodes.map((n) => [n.id, []]));
  for (const e of city.edges) { at.get(e.a)?.push(e); if (e.b !== e.a) at.get(e.b)?.push(e); }
  const b = city.bounds, T = 1e-6;
  const cum = new Map(city.edges.map((e) => [e.id, cumLengths(e.pts)]));
  return {
    city, nodes, edges, at, cum,
    len: (e) => cum.get(e.id).at(-1),
    border: (n) => n.x <= b.minx + T || n.x >= b.maxx - T || n.y <= b.miny + T || n.y >= b.maxy - T,
    atBorder: (x, y, tol = 0.5) => x <= b.minx + tol || x >= b.maxx - tol || y <= b.miny + tol || y >= b.maxy - tol,
  };
}

// ── R1-R5 ────────────────────────────────────────────────────────────────────
function r1(c, add) {
  for (const n of c.city.nodes) {
    const es = c.at.get(n.id);
    if (es.length === 1 && !c.border(n) && es[0].cls !== "tertiary") add("R1", `a ${es[0].cls} road dead-ends here (only tertiary may)`, n.x, n.y, { nodeId: n.id, edgeId: es[0].id });
  }
}
function r2(c, add) {
  for (const e of c.city.edges) {
    if (!isStreetPrimary(e)) continue;
    for (const nid of [e.a, e.b]) {
      const n = c.nodes.get(nid);
      if (!n || c.border(n)) continue;
      if (!c.at.get(nid).some((o) => o !== e && isStreetPrimary(o))) add("R2", "a primary road ends where no other primary road is", n.x, n.y, { nodeId: nid, edgeId: e.id });
    }
  }
}
function r3(c, add) {
  for (const e of c.city.edges) {
    if (e.cls !== "secondary") continue;
    for (const nid of [e.a, e.b]) {
      const n = c.nodes.get(nid);
      if (!n || c.border(n)) continue;
      const ok = c.at.get(nid).some((o) => o !== e && (isStreetPrimary(o) || o.cls === "secondary" || (e.ramp && o.highway)));
      if (!ok) add("R3", "a secondary road ends where no primary or secondary road is", n.x, n.y, { nodeId: nid, edgeId: e.id });
    }
  }
}
function r4(c, add) {
  for (const n of c.city.nodes) {
    const es = c.at.get(n.id), hw = es.filter((e) => e.highway), other = es.filter((e) => !e.highway);
    if (!hw.length) continue;
    for (const o of other) if (!o.ramp) add("R4", `a ${o.cls} road meets a highway at ground level (only ramps may)`, n.x, n.y, { nodeId: n.id, edgeId: o.id });
    if (hw.length === 1 && !c.border(n)) add("R4", "a highway stops inside the map", n.x, n.y, { nodeId: n.id });
    if (hw.length > 2) add("R4", "a highway has a junction with another highway", n.x, n.y, { nodeId: n.id });
  }
}
function r5(c, add) {
  const hwAt = (nid) => c.at.get(nid).some((e) => e.highway);
  for (const e of c.city.edges) {
    if (!e.ramp) continue;
    const ma = c.nodes.get(e.a), mb = c.nodes.get(e.b);
    if (e.cls !== "secondary" || e.highway) add("R5", "a ramp must be a secondary road", ma.x, ma.y, { edgeId: e.id });
    const ha = hwAt(e.a), hb = hwAt(e.b);
    if (ha === hb) { add("R5", "a ramp must have exactly one end on a highway", ma.x, ma.y, { edgeId: e.id }); continue; }
    const street = ha ? mb : ma;
    const ok = c.at.get(street.id).some((o) => o !== e && (isStreetPrimary(o) || (o.cls === "secondary" && !o.ramp)));
    if (!ok) add("R5", "a ramp must join a primary or secondary road, never a tertiary one", street.x, street.y, { edgeId: e.id });
  }
  // every highway needs ramps, spaced 1-3 km (spacing is a soft target)
  const hAt = new Map();
  for (const e of c.city.edges) if (e.highway) for (const nid of [e.a, e.b]) (hAt.get(nid) || hAt.set(nid, []).get(nid)).push(e);
  const seen = new Set();
  for (const [start, es] of hAt) {
    if (es.length !== 1 || seen.has(start)) continue;
    let cur = start, prev = null, pos = 0;
    const ramps = [];
    const first = c.nodes.get(start);
    for (let guard = 0; guard < 1e5; guard++) {
      seen.add(cur);
      if (c.at.get(cur).some((e) => e.ramp)) ramps.push(pos);
      const next = (hAt.get(cur) || []).find((e) => e !== prev);
      if (!next) break;
      pos += c.len(next);
      cur = next.a === cur ? next.b : next.a;
      prev = next;
    }
    if (ramps.length < 2) add("R5", `a highway has ${ramps.length} ramp${ramps.length === 1 ? "" : "s"} (needs at least 2)`, first.x, first.y);
    const lo = LIMITS.rampSpacing[0] * (1 - LIMITS.rampSlack), hi = LIMITS.rampSpacing[1] * (1 + LIMITS.rampSlack);
    for (let i = 1; i < ramps.length; i++) {
      const d = ramps[i] - ramps[i - 1];
      if (d < lo || d > hi) add("R5", `two ramps are ${Math.round(d)} m apart (target ${LIMITS.rampSpacing[0]}-${LIMITS.rampSpacing[1]} m)`, first.x, first.y, { severity: "warn" });
    }
  }
}

// ── R6 ───────────────────────────────────────────────────────────────────────
function r6(c, add) {
  const par = new Map(c.city.nodes.map((n) => [n.id, n.id]));
  const find = (x) => { while (par.get(x) !== x) { par.set(x, par.get(par.get(x))); x = par.get(x); } return x; };
  for (const e of c.city.edges) if (par.has(e.a) && par.has(e.b)) par.set(find(e.a), find(e.b));
  const comps = new Map();
  for (const e of c.city.edges) { const r = find(e.a); (comps.get(r) || comps.set(r, []).get(r)).push(e); }
  if (comps.size < 2) return;
  let big = null;
  for (const [r, l] of comps) if (!big || l.length > comps.get(big).length) big = r;
  for (const [r, l] of comps) {
    if (r === big) continue;
    const n = c.nodes.get(l[0].a);
    add("R6", `${l.length} road${l.length === 1 ? "" : "s"} are cut off from the rest of the network`, n.x, n.y, { nodeId: n.id });
  }
}

// ── R7 ───────────────────────────────────────────────────────────────────────
function r7(c, add) {
  const { city } = c;
  const grid = createGrid(100);
  for (const e of city.edges) for (let j = 0; j < e.pts.length / 2 - 1; j++) grid.insertSegment(e.id * SEG + j, e.pts[2 * j], e.pts[2 * j + 1], e.pts[2 * j + 2], e.pts[2 * j + 3]);

  const recs = new Map();
  for (const x of city.crossings || []) if (x.overEdge != null && x.underEdge != null) { const k = pairKey(x.overEdge, x.underEdge); (recs.get(k) || recs.set(k, []).get(k)).push(x); }
  const nodeNear = (e, x, y) => [e.a, e.b].some((id) => { const n = c.nodes.get(id); return n && Math.hypot(n.x - x, n.y - y) < LIMITS.touchTol * 4; });

  for (const e1 of city.edges) {
    for (let j1 = 0; j1 < e1.pts.length / 2 - 1; j1++) {
      const [ax, ay, bx, by] = [e1.pts[2 * j1], e1.pts[2 * j1 + 1], e1.pts[2 * j1 + 2], e1.pts[2 * j1 + 3]];
      const code1 = e1.id * SEG + j1;
      for (const code2 of grid.querySegment(ax, ay, bx, by)) {
        if (code2 <= code1) continue;
        const id2 = Math.floor(code2 / SEG), j2 = code2 - id2 * SEG;
        if (id2 === e1.id) continue;
        const e2 = c.edges.get(id2);
        const [cx, cy, dx, dy] = [e2.pts[2 * j2], e2.pts[2 * j2 + 1], e2.pts[2 * j2 + 2], e2.pts[2 * j2 + 3]];
        const h = segIntersect(ax, ay, bx, by, cx, cy, dx, dy);
        if (h) {
          const shared = [e1.a, e1.b].filter((id) => id === e2.a || id === e2.b);
          if (shared.some((id) => { const n = c.nodes.get(id); return Math.hypot(n.x - h.x, n.y - h.y) < LIMITS.touchTol * 4; })) continue;
          if (nodeNear(e1, h.x, h.y) || nodeNear(e2, h.x, h.y)) { add("R7", "a road touches another road without a junction", h.x, h.y, { edgeId: e1.id }); continue; }
          const rec = (recs.get(pairKey(e1.id, e2.id)) || []).find((r) => Math.hypot(r.x - h.x, r.y - h.y) < 3);
          if (!rec) add("R7", "two roads cross without a junction, bridge or overpass", h.x, h.y, { edgeId: e1.id });
          else if (c.edges.get(rec.overEdge).layer <= c.edges.get(rec.underEdge).layer) add("R7", "a crossing is recorded but both roads are at the same level", h.x, h.y, { edgeId: e1.id });
        } else if (collinearOverlap(ax, ay, bx, by, cx, cy, dx, dy)) add("R7", "two roads run on top of each other", (ax + bx) / 2, (ay + by) / 2, { edgeId: e1.id });
      }
    }
  }
  for (const n of city.nodes) {
    const es = c.at.get(n.id);
    for (const code of grid.queryBox(n.x - 0.2, n.y - 0.2, n.x + 0.2, n.y + 0.2)) {
      const id = Math.floor(code / SEG), j = code - id * SEG, e = c.edges.get(id);
      if (e.a === n.id || e.b === n.id) continue;
      if (closestOnSeg(n.x, n.y, e.pts[2 * j], e.pts[2 * j + 1], e.pts[2 * j + 2], e.pts[2 * j + 3]).d < LIMITS.touchTol) add("R7", "a junction lies on a road that is not connected to it", n.x, n.y, { nodeId: n.id });
    }
    if (es.length === 1 && !c.border(n)) {
      for (const code of grid.queryBox(n.x - LIMITS.nearMiss, n.y - LIMITS.nearMiss, n.x + LIMITS.nearMiss, n.y + LIMITS.nearMiss)) {
        const id = Math.floor(code / SEG), j = code - id * SEG, e = c.edges.get(id);
        if (e.a === n.id || e.b === n.id) continue;
        if (closestOnSeg(n.x, n.y, e.pts[2 * j], e.pts[2 * j + 1], e.pts[2 * j + 2], e.pts[2 * j + 3]).d < LIMITS.nearMiss) add("R7", "a dead end stops just short of a road without joining it", n.x, n.y, { nodeId: n.id });
      }
    }
  }
  for (const x of city.crossings || []) {
    if (x.kind === "overpass") {
      const o = c.edges.get(x.overEdge), u = c.edges.get(x.underEdge);
      if (!o || !u) { add("R7", "a crossing record points at a road that does not exist", x.x, x.y); continue; }
      if (o.layer <= u.layer) add("R7", "an overpass has the upper road at the same level as the lower one", x.x, x.y);
      for (const e of [o, u]) if (closestOnPolyline(e.pts, c.cum.get(e.id), x.x, x.y).d > 1.5) add("R7", "a crossing record does not match where the roads are", x.x, x.y);
    } else if (x.kind === "bridge") {
      const o = c.edges.get(x.overEdge);
      if (!o || !o.bridge) add("R7", "a bridge record does not point at a bridge", x.x, x.y);
    } else if (x.kind === "rail_over") {
      if (!(city.rails || []).some((r) => r.id === x.rail) || !c.edges.get(x.underEdge)) add("R7", "a rail crossing record is broken", x.x, x.y);
    }
  }
}

// ── R8, R9 ───────────────────────────────────────────────────────────────────
function r8r9(c, add) {
  for (const n of c.city.nodes) {
    const es = c.at.get(n.id);
    const hwOrRamp = es.some((e) => e.highway || e.ramp);
    if (n.type === "roundabout") {
      if (es.length < 5) add("R8", `a roundabout with only ${es.length} roads (needs 5+)`, n.x, n.y, { nodeId: n.id });
      if (es.some((e) => e.cls === "tertiary")) add("R8", "a roundabout has a tertiary street on it", n.x, n.y, { nodeId: n.id });
      if (hwOrRamp) add("R8", "a roundabout sits on a highway or ramp", n.x, n.y, { nodeId: n.id });
      if (!(n.radius > 0)) add("R8", "a roundabout has no radius", n.x, n.y, { nodeId: n.id });
    }
    if (n.type === "signal") {
      if (es.length < 3) add("R9", `traffic lights with only ${es.length} roads`, n.x, n.y, { nodeId: n.id });
      if (es.filter((e) => e.cls !== "tertiary").length < 2) add("R9", "traffic lights where fewer than two roads are non-tertiary", n.x, n.y, { nodeId: n.id });
      if (hwOrRamp) add("R9", "traffic lights on a highway or ramp", n.x, n.y, { nodeId: n.id });
    }
  }
}

// ── R10 ──────────────────────────────────────────────────────────────────────
function r10(c, add) {
  const water = c.city.water || [];
  if (!water.length) return;
  const boxes = water.map((w) => { const xs = w.polygon.map((p) => p[0]), ys = w.polygon.map((p) => p[1]); return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)]; });
  const kindAt = (x, y) => { for (let i = 0; i < water.length; i++) { const b = boxes[i]; if (x >= b[0] && x <= b[2] && y >= b[1] && y <= b[3] && pointInPolygon(x, y, water[i].polygon)) return water[i].kind; } return null; };
  const bridges = [];
  for (const e of c.city.edges) {
    const cum = c.cum.get(e.id), L = cum.at(-1), n = Math.max(2, Math.ceil(L / 10));
    let wet = null, wx = 0, wy = 0;
    for (let k = 0; k <= n; k++) {
      const s = (L * k) / n;
      let i = 0; while (i < cum.length - 2 && cum[i + 1] < s) i++;
      const t = cum[i + 1] > cum[i] ? (s - cum[i]) / (cum[i + 1] - cum[i]) : 0;
      const x = e.pts[2 * i] + (e.pts[2 * i + 2] - e.pts[2 * i]) * t, y = e.pts[2 * i + 1] + (e.pts[2 * i + 3] - e.pts[2 * i + 1]) * t;
      const w = kindAt(x, y);
      if (w) { wet = w; wx = x; wy = y; break; }
    }
    if (!wet) continue;
    const mayBridge = wet === "river" && e.bridge && (e.highway || (!e.ramp && (e.cls === "primary" || e.cls === "secondary")));
    if (!mayBridge) add("R10", wet === "river" && !e.bridge ? "a road runs through a river without a bridge" : `a road goes ${wet === "river" ? "over a river on a road type that may not bridge" : "through a " + wet}`, wx, wy, { edgeId: e.id });
    else bridges.push({ id: e.id, x: wx, y: wy });
  }
  for (let i = 0; i < bridges.length; i++) for (let j = i + 1; j < bridges.length; j++) {
    const d = Math.hypot(bridges[i].x - bridges[j].x, bridges[i].y - bridges[j].y);
    if (d < LIMITS.bridgeSpacing[0]) add("R10", `two bridges are only ${Math.round(d)} m apart`, bridges[i].x, bridges[i].y, { severity: "warn" });
  }
}

// ── R11 ──────────────────────────────────────────────────────────────────────
function r11(c, add) {
  const { city } = c;
  const recs = (city.crossings || []).filter((x) => x.kind === "rail_over");
  for (const r of city.rails || []) {
    const n = r.pts.length / 2;
    for (const [x, y] of [[r.pts[0], r.pts[1]], [r.pts[2 * n - 2], r.pts[2 * n - 1]]]) if (!c.atBorder(x, y)) add("R11", "a railway ends inside the map", x, y);
    const cum = cumLengths(r.pts);
    for (let i = 0; i < n - 1; i++) {
      const [ax, ay, bx, by] = [r.pts[2 * i], r.pts[2 * i + 1], r.pts[2 * i + 2], r.pts[2 * i + 3]];
      for (const e of city.edges) {
        for (let j = 0; j < e.pts.length / 2 - 1; j++) {
          const h = segIntersect(ax, ay, bx, by, e.pts[2 * j], e.pts[2 * j + 1], e.pts[2 * j + 2], e.pts[2 * j + 3]);
          if (h && !recs.some((x) => x.rail === r.id && x.underEdge === e.id && Math.hypot(x.x - h.x, x.y - h.y) < 3)) add("R11", "a railway crosses a road at ground level", h.x, h.y, { edgeId: e.id });
        }
      }
    }
    for (const nd of city.nodes) {
      const d = closestOnPolyline(r.pts, cum, nd.x, nd.y).d;
      if (d < r.width / 2 + 1) add("R11", "a railway runs through a junction", nd.x, nd.y, { nodeId: nd.id });
    }
  }
}

// ── R12 ──────────────────────────────────────────────────────────────────────
function r12(c, add) {
  const { city } = c, b = city.bounds, T = 1e-6;
  const inside = (x, y) => x >= b.minx - T && x <= b.maxx + T && y >= b.miny - T && y <= b.maxy + T;
  const pairs = new Map();
  for (const e of city.edges) {
    const a = c.nodes.get(e.a), z = c.nodes.get(e.b);
    if (!a || !z) { add("R12", "a road points at a node that does not exist", e.pts[0], e.pts[1], { edgeId: e.id }); continue; }
    if (e.a === e.b) add("R12", "a road starts and ends at the same node", a.x, a.y, { edgeId: e.id });
    const n = e.pts.length / 2;
    if (Math.hypot(e.pts[0] - a.x, e.pts[1] - a.y) > 1e-6 || Math.hypot(e.pts[2 * n - 2] - z.x, e.pts[2 * n - 1] - z.y) > 1e-6) add("R12", "a road's line does not start and end on its nodes", a.x, a.y, { edgeId: e.id });
    const len = c.len(e), mid = e.pts.slice(2 * Math.floor(n / 2), 2 * Math.floor(n / 2) + 2);
    if (len < LIMITS.minEdge - 1e-6) add("R12", `an edge is only ${len.toFixed(1)} m long (min ${LIMITS.minEdge} m)`, mid[0], mid[1], { edgeId: e.id });
    const self = polylineSelfIntersection(e.pts);
    if (self) add("R12", "a road crosses itself", self.x, self.y, { edgeId: e.id });
    for (let i = 0; i < n; i++) if (!inside(e.pts[2 * i], e.pts[2 * i + 1])) { add("R12", "a road leaves the map", e.pts[2 * i], e.pts[2 * i + 1], { edgeId: e.id }); break; }
    const k = pairKey(e.a, e.b);
    (pairs.get(k) || pairs.set(k, []).get(k)).push({ e, len });
  }
  for (const list of pairs.values()) {
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const A = list[i].e, B = list[j].e, m = pointAtMid(A, c.cum.get(A.id));
      if (closestOnPolyline(B.pts, c.cum.get(B.id), m[0], m[1]).d < 5) add("R12", "two roads connect the same two junctions along the same line", m[0], m[1], { edgeId: A.id });
    }
  }
  for (const n of city.nodes) {
    const es = c.at.get(n.id);
    if (!es.length) { add("R12", "a node with no roads", n.x, n.y, { nodeId: n.id }); continue; }
    if (!inside(n.x, n.y)) add("R12", "a node is outside the map", n.x, n.y, { nodeId: n.id });
    const t = es.map((e) => tangentAt(e.pts, e.a === n.id));
    for (let i = 0; i < t.length; i++) for (let j = i + 1; j < t.length; j++) {
      if (angleDeg(t[i][0], t[i][1], t[j][0], t[j][1]) < LIMITS.minAngle) add("R12", `two roads meet at less than ${LIMITS.minAngle}°`, n.x, n.y, { nodeId: n.id });
    }
  }
  const ns = city.nodes;
  const cell = new Map();
  for (const n of ns) { const k = `${Math.floor(n.x)},${Math.floor(n.y)}`; (cell.get(k) || cell.set(k, []).get(k)).push(n); }
  for (const n of ns) for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) for (const o of cell.get(`${Math.floor(n.x) + dx},${Math.floor(n.y) + dy}`) || []) {
    if (o.id > n.id && Math.hypot(o.x - n.x, o.y - n.y) < LIMITS.mergeTol) add("R12", "two junctions sit on the same spot", n.x, n.y, { nodeId: n.id });
  }
}
function pointAtMid(e, cum) {
  const half = cum.at(-1) / 2;
  let i = 0; while (i < cum.length - 2 && cum[i + 1] < half) i++;
  const t = cum[i + 1] > cum[i] ? (half - cum[i]) / (cum[i + 1] - cum[i]) : 0;
  return [e.pts[2 * i] + (e.pts[2 * i + 2] - e.pts[2 * i]) * t, e.pts[2 * i + 1] + (e.pts[2 * i + 3] - e.pts[2 * i + 1]) * t];
}

// ── R13 (only once roads exist) ──────────────────────────────────────────────
function r13(c, add) {
  const roads = c.city.roads || [];
  if (!roads.length) return;
  const med = (a) => { const s = a.slice().sort((p, q) => p - q); return s.length ? s[Math.floor(s.length / 2)] : 0; };
  const by = (cls) => roads.filter((r) => r.cls === cls).map((r) => r.length);
  const xs = c.city.nodes.map((n) => n.x), ys = c.city.nodes.map((n) => n.y);
  const diameter = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys));
  const block = c.city.meta?.params?.blockSize || 100;
  const p = by("primary"), s = by("secondary"), t = by("tertiary");
  const warn = (m) => add("R13", m, 0, 0, { severity: "warn" });
  if (p.length && med(p) < LIMITS.primaryVsCity * diameter) warn(`median primary road is ${Math.round(med(p))} m (target ${Math.round(LIMITS.primaryVsCity * diameter)} m+)`);
  if (s.length && med(s) < LIMITS.secondaryBlocks * block) warn(`median secondary road is ${Math.round(med(s))} m (target ${LIMITS.secondaryBlocks * block} m+)`);
  if (s.length && t.length && med(t) >= med(s)) warn("tertiary roads are not shorter than secondary roads");
}

// ── R14 ──────────────────────────────────────────────────────────────────────
function r14(c, add) {
  for (const n of c.city.nodes) {
    if (!c.border(n)) continue;
    for (const e of c.at.get(n.id)) if (e.cls === "tertiary") add("R14", "a tertiary street reaches the map edge", n.x, n.y, { nodeId: n.id, edgeId: e.id });
  }
  const fp = c.city.footprint;
  if (fp && fp.length > 2) {
    for (const e of c.city.edges) {
      if (e.cls !== "tertiary") continue;
      for (let i = 0; i < e.pts.length / 2; i++) if (!pointInPolygon(e.pts[2 * i], e.pts[2 * i + 1], fp)) { add("R14", "a tertiary street lies outside the city footprint", e.pts[2 * i], e.pts[2 * i + 1], { edgeId: e.id }); break; }
    }
  }
}

const CHECKS = [r1, r2, r3, r4, r5, r6, r7, r8r9, r10, r11, r12, r13, r14];

export function validate(city) {
  const c = buildCtx(city), out = [], seen = new Set();
  const add = (rule, message, x, y, extra = {}) => {
    const k = `${rule}|${message}|${Math.round(x / 2)}|${Math.round(y / 2)}`;
    if (seen.has(k)) return;
    seen.add(k);
    out.push({ rule, message, x, y, severity: "error", ...extra });
  };
  for (const check of CHECKS) check(c, add);
  return out;
}

export const hardViolations = (v) => v.filter((x) => x.severity === "error");
export function summarize(violations) {
  const by = {};
  for (const v of violations) by[v.rule] = (by[v.rule] || 0) + 1;
  return by;
}
