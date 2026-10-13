// pieces.js: cut one polyline into edges at a list of marks (junctions, bridge ends).

import { LIMITS } from "./params.js";
import { slicePolyline } from "./geom.js";

/**
 * marks: [{ s, x, y, ref, span?, spanEnd? }]; must include s = 0 and s = full length.
 * span = id of a bridge span this mark opens, spanEnd = id of the span it closes.
 * Returns { pieces: [{ a, b, s0, s1, bridge, pts }] } or { error, x, y }.
 */
export function subdivide(pts, cum, marks, minLen = LIMITS.minEdge) {
  const m = marks.slice().sort((p, q) => p.s - q.s);
  const pieces = [];
  for (let i = 0; i < m.length - 1; i++) {
    const a = m[i], b = m[i + 1], len = b.s - a.s;
    const mid = { x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 };
    if (len < minLen - 1e-6) return { error: len < 0.05 ? "two junctions at the same place" : `it would leave an edge only ${len.toFixed(0)} m long`, ...mid };
    const bridge = a.span != null;
    if (bridge && b.spanEnd !== a.span) return { error: "a junction would land on a bridge", ...mid };
    pieces.push({ a: a.ref, b: b.ref, s0: a.s, s1: b.s, bridge, pts: slicePolyline(pts, cum, a.s, b.s, a, b) });
  }
  return { pieces };
}
