// demo.js: hand-built cities for testing the core and for the viewer. No generator involved.
//   town    - small valid town: primaries, a secondary ring, tertiary streets, a roundabout, signals
//   highway - valid: highway on overpasses, two ramps, a railway on bridges
//   broken  - every rule broken on purpose, so the debug overlay has something to show

import { boundsFor, midWidth, widthKind } from "./params.js";
import { createNetwork } from "./network.js";
import { assembleCity } from "./city.js";
import { validate } from "./validate.js";

export const DEMOS = [
  { key: "town", label: "Small town", note: "Valid by construction." },
  { key: "highway", label: "Highway and rail", note: "Valid: overpasses, ramps, railway." },
  { key: "broken", label: "Every rule broken", note: "Each red marker is a rule violation." },
];

function must(net, res, label) {
  if (!res.ok) throw new Error(`demo road "${label}" was refused: ${res.error} (${Math.round(res.x)}, ${Math.round(res.y)})`);
  return res;
}

function town() {
  const net = createNetwork(boundsFor(2));
  const add = (label, pts, cls, opts) => must(net, net.tryAddRoad(pts, cls, opts), label);
  add("east-west", [-1000, 0, 1000, 0], "primary");
  add("north-south", [0, -1000, 0, 1000], "primary");
  add("diagonal", [-1000, -1000, 1000, 1000], "primary");
  add("ring NW", [-400, 0, -400, 300, -300, 400, 0, 400], "secondary");
  add("ring NE", [400, 0, 400, 300, 300, 400, 0, 400], "secondary");
  add("ring SW", [-400, 0, -400, -300, -300, -400, 0, -400], "secondary");
  add("ring SE", [400, 0, 400, -300, 300, -400, 0, -400], "secondary");
  add("street y=200", [-400, 200, 400, 200], "tertiary");
  add("street y=-200", [-400, -200, 400, -200], "tertiary");
  add("street x=-200", [-200, -400, -200, 400], "tertiary");
  add("street x=200", [200, -400, 200, 400], "tertiary");
  add("cul-de-sac W", [-700, 0, -700, 120], "tertiary");
  add("cul-de-sac E", [700, 0, 700, -130], "tertiary");
  add("cul-de-sac N", [-300, 200, -300, 320], "tertiary");
  const g = net.graph, at = (x, y) => g.nearestNode(x, y, 5);
  g.setNodeKind(at(0, 0), "roundabout", 26);
  for (const [x, y] of [[0, 400], [-400, 0], [400, 0]]) g.setNodeKind(at(x, y), "signal");
  return assembleCity(net, { meta: { style: "planned", sizeKm: 2 } });
}

function highway() {
  const net = createNetwork(boundsFor(4));
  const add = (label, pts, cls, opts) => must(net, net.tryAddRoad(pts, cls, opts), label);
  add("highway", [-2000, -1100, -1000, -1250, 0, -1300, 1000, -1250, 2000, -1100], "primary", { highway: true });
  add("north-south W", [-600, -2000, -600, 2000], "primary");
  add("north-south E", [600, -2000, 600, 2000], "primary");
  add("east-west", [-2000, 0, 2000, 0], "primary");
  add("ramp W", [-900, -1260, -800, -1150, -700, -1000, -600, -900], "secondary", { ramp: true, highwayEnd: 0 });
  add("ramp E", [900, -1260, 800, -1150, 700, -1000, 600, -900], "secondary", { ramp: true, highwayEnd: 0 });
  add("ring road", [-600, 700, -300, 850, 300, 850, 600, 700], "secondary");
  add("cross street", [-600, -400, 600, -400], "tertiary");
  add("side street", [0, -400, 0, -250], "tertiary");
  must(net, net.addRail([-2000, 300, -500, 350, 500, 350, 2000, 300]), "railway");
  return assembleCity(net, { meta: { style: "planned", sizeKm: 4 } });
}

// ── hand-made broken city ────────────────────────────────────────────────────
function raw({ sizeKm, nodes, edges, water = [], rails = [], crossings = [] }) {
  const b = boundsFor(sizeKm), T = 1e-6;
  const border = (x, y) => x <= b.minx + T || x >= b.maxx - T || y <= b.miny + T || y >= b.maxy - T;
  const deg = new Map();
  for (const e of edges) { deg.set(e.a, (deg.get(e.a) || 0) + 1); deg.set(e.b, (deg.get(e.b) || 0) + 1); }
  const N = Object.entries(nodes).map(([id, [x, y, type, radius]]) => ({
    id: Number(id), x, y, type: type || (border(x, y) ? "map_edge" : deg.get(Number(id)) === 1 ? "end" : "plain"), ...(radius ? { radius } : {}),
  }));
  const at = new Map(N.map((n) => [n.id, n]));
  const E = edges.map((e, i) => {
    const a = at.get(e.a), z = at.get(e.b);
    const e2 = { id: i, cls: e.cls, highway: !!e.highway, ramp: !!e.ramp, layer: e.layer ?? 0, bridge: !!e.bridge, roadId: null };
    return { ...e2, a: e.a, b: e.b, pts: [a.x, a.y, ...(e.via || []), z.x, z.y], width: midWidth(widthKind(e2)) };
  });
  const city = {
    meta: { version: 1, seed: 0, style: "planned", sizeKm, params: {} }, bounds: b, water, footprint: null,
    nodes: N, edges: E, roads: [], rails, crossings,
    stats: { km: { highway: 0, primary: 0, secondary: 0, tertiary: 0, rail: 0 }, edges: E.length, nodes: { total: N.length, junctions: 0, deadEnds: 0, mapEdge: 0 }, counts: { roundabouts: 0, signals: 0, ramps: 0, bridges: 0, overpasses: 0, railHighwayCrossings: 0 }, ms: 0 },
  };
  for (const e of E) {
    let l = 0;
    for (let i = 2; i < e.pts.length; i += 2) l += Math.hypot(e.pts[i] - e.pts[i - 2], e.pts[i + 1] - e.pts[i - 1]);
    city.stats.km[e.highway ? "highway" : e.cls] += l / 1000;
  }
  city.violations = validate(city);
  return city;
}

function broken() {
  const P = "primary", S = "secondary", T = "tertiary";
  return raw({
    sizeKm: 4,
    nodes: {
      1: [-2000, 0], 9: [-800, 0], 2: [0, 0], 7: [800, 0, "roundabout", 25], 3: [2000, 0],
      4: [0, -2000], 21: [0, -1100], 5: [0, 2000],
      8: [800, 500], 10: [-800, -400, "signal"], 11: [-1100, -400], 15: [1200, -300], 16: [1500, -300],
      17: [-2000, -1500], 18: [0, -1500], 25: [1000, -1500], 19: [2000, -1500], 26: [1000, -1100], 27: [1400, -1100],
      28: [-1500, 1200], 29: [-1000, 1200],
      31: [-600, 800], 32: [-100, 1300], 33: [-600, 1300], 34: [-100, 800],
      37: [900, 900], 38: [1500, 900], 39: [1200, 900], 40: [1200, 1300],
      44: [1300, 750], 45: [1800, 750],
      46: [-300, -800], 47: [-290, -800], 48: [300, -800], 49: [500, -800], 50: [500, -765],
      52: [2000, -300], 53: [1700, -300],
    },
    edges: [
      { a: 1, b: 9, cls: P }, { a: 9, b: 2, cls: P }, { a: 2, b: 7, cls: P }, { a: 7, b: 3, cls: P },
      { a: 4, b: 21, cls: P }, { a: 21, b: 2, cls: P }, { a: 2, b: 5, cls: P },
      { a: 7, b: 8, cls: S },                                  // R1, R3: secondary dead end
      { a: 9, b: 10, cls: P }, { a: 10, b: 11, cls: T },       // R2: primary ends on a tertiary
      { a: 7, b: 15, cls: S }, { a: 15, b: 16, cls: T },       // R3: secondary ends on a tertiary
      { a: 17, b: 18, cls: P, highway: true }, { a: 18, b: 25, cls: P, highway: true }, { a: 25, b: 19, cls: P, highway: true },
      { a: 18, b: 21, cls: T },                                // R4: street meets the highway
      { a: 25, b: 26, cls: S, ramp: true }, { a: 26, b: 27, cls: T }, // R5: ramp onto a tertiary street
      { a: 28, b: 29, cls: T },                                // R6: cut off from the rest
      { a: 31, b: 32, cls: T }, { a: 33, b: 34, cls: T },      // R7: crossing without a node
      { a: 37, b: 38, cls: T }, { a: 39, b: 40, cls: T },      // R7: node on a road it is not part of
      { a: 44, b: 45, cls: T },                                // R10: street through a lake
      { a: 46, b: 47, cls: T },                                // R12: 10 m edge
      { a: 48, b: 49, cls: T }, { a: 48, b: 50, cls: T },      // R12: 10 degree junction
      { a: 52, b: 53, cls: T },                                // R14: tertiary at the map edge
    ],
    water: [
      { kind: "lake", polygon: [[1500, 600], [1900, 600], [1900, 900], [1500, 900]] },
      { kind: "river", polygon: [[-1300, -2000], [-1250, -2000], [-1250, 2000], [-1300, 2000]] },
    ],
    rails: [{ id: 0, pts: [-2000, 600, 2000, 700], width: 3.5 }], // R11: crosses the north-south road at ground level
  });
}

export function buildDemo(key) {
  if (key === "town") return town();
  if (key === "highway") return highway();
  if (key === "broken") return broken();
  throw new Error(`unknown demo "${key}"`);
}
