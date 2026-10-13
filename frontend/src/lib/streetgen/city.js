// city.js: turn the working graph into the plain City object the viewer and the game read, and validate it.

import { VERSION } from "./params.js";
import { validate } from "./validate.js";

export function assembleCity(net, extra = {}) {
  const g = net.graph, b = g.bounds;
  const typeOf = (n) => {
    const es = g.edgesAt(n.id);
    if (es.some((e) => e.highway) && es.some((e) => e.ramp)) return "ramp";
    if (n.kind === "roundabout" || n.kind === "signal") return n.kind;
    if (g.onBorder(n.x, n.y)) return "map_edge";
    return es.length === 1 ? "end" : "plain";
  };
  const nodes = g.allNodes().map((n) => ({ id: n.id, x: n.x, y: n.y, type: typeOf(n), ...(n.radius ? { radius: n.radius } : {}) }));
  const edges = g.allEdges().map((e) => ({
    id: e.id, a: e.a, b: e.b, cls: e.cls, highway: e.highway, ramp: e.ramp, pts: e.pts.slice(),
    width: e.width, layer: e.layer, bridge: e.bridge, roadId: extra.roadIds?.get(e.id) ?? null,
  }));
  const rails = net.rails.map((r) => ({ id: r.id, pts: r.pts.slice(), width: r.width }));
  const crossings = g.crossings.map((c) => ({ ...c }));

  const km = { highway: 0, primary: 0, secondary: 0, tertiary: 0, rail: 0 };
  for (const e of g.allEdges()) km[e.highway ? "highway" : e.cls] += e.len / 1000;
  for (const r of g.allEdges().length ? net.rails : []) { let l = 0; for (let i = 2; i < r.pts.length; i += 2) l += Math.hypot(r.pts[i] - r.pts[i - 2], r.pts[i + 1] - r.pts[i - 1]); km.rail += l / 1000; }
  const count = (t) => nodes.filter((n) => n.type === t).length;
  const deg = (id) => g.edgesAt(id).length;
  const stats = {
    km, edges: edges.length,
    nodes: { total: nodes.length, junctions: nodes.filter((n) => deg(n.id) >= 3).length, deadEnds: nodes.filter((n) => n.type === "end").length, mapEdge: count("map_edge") },
    counts: {
      roundabouts: count("roundabout"), signals: count("signal"), ramps: edges.filter((e) => e.ramp).length,
      bridges: edges.filter((e) => e.bridge).length, overpasses: crossings.filter((c) => c.kind === "overpass").length,
      railHighwayCrossings: crossings.filter((c) => c.kind === "rail_over" && g.edge(c.underEdge)?.highway).length,
    },
    ms: extra.ms ?? 0,
  };

  const city = {
    meta: { version: VERSION, seed: 0, style: "planned", sizeKm: (b.maxx - b.minx) / 1000, params: {}, ...(extra.meta || {}) },
    bounds: { ...b },
    water: extra.water || [],
    footprint: extra.footprint || null,
    nodes, edges, roads: extra.roads || [], rails, crossings, stats,
  };
  city.violations = extra.validate === false ? [] : validate(city);
  return city;
}
