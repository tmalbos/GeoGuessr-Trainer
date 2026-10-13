// geom.js: small geometry helpers. A polyline is a flat array [x0, y0, x1, y1, ...] in metres.

export const dist = (ax, ay, bx, by) => Math.hypot(bx - ax, by - ay);
const clamp01 = (v) => (v < 0 ? 0 : v > 1 ? 1 : v);

export function cumLengths(pts) {
  const n = pts.length / 2, c = new Array(n);
  c[0] = 0;
  for (let i = 1; i < n; i++) c[i] = c[i - 1] + Math.hypot(pts[2 * i] - pts[2 * i - 2], pts[2 * i + 1] - pts[2 * i - 1]);
  return c;
}
export const polyLength = (pts) => { const c = cumLengths(pts); return c[c.length - 1]; };

/** Point at arclength s (clamped to the line). */
export function pointAt(pts, cum, s) {
  const n = cum.length;
  if (s <= 0) return { x: pts[0], y: pts[1], seg: 0, t: 0 };
  if (s >= cum[n - 1]) return { x: pts[2 * n - 2], y: pts[2 * n - 1], seg: n - 2, t: 1 };
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) { const m = (lo + hi) >> 1; if (cum[m] <= s) lo = m; else hi = m; }
  const len = cum[lo + 1] - cum[lo], t = len > 0 ? (s - cum[lo]) / len : 0;
  return { x: pts[2 * lo] + (pts[2 * lo + 2] - pts[2 * lo]) * t, y: pts[2 * lo + 1] + (pts[2 * lo + 3] - pts[2 * lo + 1]) * t, seg: lo, t };
}

/** The part of a polyline between arclengths s0 and s1. start/end override the exact end coordinates. */
export function slicePolyline(pts, cum, s0, s1, start, end) {
  const p0 = pointAt(pts, cum, s0), p1 = pointAt(pts, cum, s1);
  const out = [start ? start.x : p0.x, start ? start.y : p0.y];
  for (let i = 0; i < cum.length; i++) if (cum[i] > s0 + 1e-9 && cum[i] < s1 - 1e-9) out.push(pts[2 * i], pts[2 * i + 1]);
  out.push(end ? end.x : p1.x, end ? end.y : p1.y);
  return out;
}

/** Proper segment intersection. Touching at ends counts (tiny tolerance). Parallel segments return null. */
export function segIntersect(ax, ay, bx, by, cx, cy, dx, dy) {
  const d1x = bx - ax, d1y = by - ay, d2x = dx - cx, d2y = dy - cy;
  const den = d1x * d2y - d1y * d2x;
  const scale = Math.hypot(d1x, d1y) * Math.hypot(d2x, d2y);
  if (scale === 0 || Math.abs(den) < 1e-10 * scale) return null;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / den;
  const u = ((cx - ax) * d1y - (cy - ay) * d1x) / den;
  const e = 1e-9;
  if (t < -e || t > 1 + e || u < -e || u > 1 + e) return null;
  const tt = clamp01(t), uu = clamp01(u);
  return { t: tt, u: uu, x: ax + d1x * tt, y: ay + d1y * tt };
}

export function closestOnSeg(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy;
  const t = l2 > 0 ? clamp01(((px - ax) * dx + (py - ay) * dy) / l2) : 0;
  const x = ax + dx * t, y = ay + dy * t;
  return { x, y, t, d: Math.hypot(px - x, py - y) };
}

/** Closest point on a polyline: { x, y, s (arclength), seg, t, d }. */
export function closestOnPolyline(pts, cum, px, py) {
  cum = cum || cumLengths(pts);
  let best = null;
  for (let i = 0; i < cum.length - 1; i++) {
    const c = closestOnSeg(px, py, pts[2 * i], pts[2 * i + 1], pts[2 * i + 2], pts[2 * i + 3]);
    if (!best || c.d < best.d) best = { ...c, seg: i };
  }
  best.s = cum[best.seg] + best.t * (cum[best.seg + 1] - cum[best.seg]);
  return best;
}

/** Do two segments run on top of each other for more than ~1 m? */
export function collinearOverlap(ax, ay, bx, by, cx, cy, dx, dy, tol = 0.05) {
  const len = Math.hypot(bx - ax, by - ay);
  if (len < 1e-9) return false;
  const ux = (bx - ax) / len, uy = (by - ay) / len;
  const perp = (px, py) => Math.abs((px - ax) * -uy + (py - ay) * ux);
  if (perp(cx, cy) > tol || perp(dx, dy) > tol) return false;
  const tc = ((cx - ax) * ux + (cy - ay) * uy), td = ((dx - ax) * ux + (dy - ay) * uy);
  const lo = Math.max(0, Math.min(tc, td)), hi = Math.min(len, Math.max(tc, td));
  return hi - lo > 1;
}

export function angleDeg(ux, uy, vx, vy) {
  const d = Math.max(-1, Math.min(1, ux * vx + uy * vy));
  return (Math.acos(d) * 180) / Math.PI;
}

/** Unit direction leaving the first (or last) point, measured over ~`chord` metres so tiny kinks don't matter. */
export function tangentAt(pts, atStart, chord = 15) {
  const n = pts.length / 2;
  const px = atStart ? pts[0] : pts[2 * n - 2], py = atStart ? pts[1] : pts[2 * n - 1];
  let qx = px, qy = py;
  for (let k = 1; k < n; k++) {
    const i = atStart ? k : n - 1 - k;
    qx = pts[2 * i]; qy = pts[2 * i + 1];
    if (Math.hypot(qx - px, qy - py) >= chord) break;
  }
  const dx = qx - px, dy = qy - py, l = Math.hypot(dx, dy);
  return l > 0 ? [dx / l, dy / l] : [0, 0];
}

export function pointInPolygon(x, y, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i], [xj, yj] = poly[j];
    if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

/** First place where a polyline crosses or touches itself (non-adjacent segments), or null. */
export function polylineSelfIntersection(pts) {
  const n = pts.length / 2;
  for (let i = 0; i < n - 1; i++) {
    for (let j = i + 2; j < n - 1; j++) {
      const h = segIntersect(pts[2 * i], pts[2 * i + 1], pts[2 * i + 2], pts[2 * i + 3], pts[2 * j], pts[2 * j + 1], pts[2 * j + 2], pts[2 * j + 3]);
      if (h) return { x: h.x, y: h.y };
    }
  }
  return null;
}

/** Clip a polyline to a rectangle. Returns the inside runs (flat arrays), in order. */
export function clipPolylineToRect(pts, minx, miny, maxx, maxy) {
  const runs = [];
  let cur = null;
  const n = pts.length / 2;
  for (let i = 0; i < n - 1; i++) {
    const ax = pts[2 * i], ay = pts[2 * i + 1], bx = pts[2 * i + 2], by = pts[2 * i + 3];
    const dx = bx - ax, dy = by - ay;
    const p = [-dx, dx, -dy, dy], q = [ax - minx, maxx - ax, ay - miny, maxy - ay];
    let t0 = 0, t1 = 1, ok = true;
    for (let k = 0; k < 4 && ok; k++) {
      if (p[k] === 0) { if (q[k] < 0) ok = false; }
      else {
        const r = q[k] / p[k];
        if (p[k] < 0) { if (r > t1) ok = false; else if (r > t0) t0 = r; }
        else if (r < t0) ok = false; else if (r < t1) t1 = r;
      }
    }
    if (!ok) { if (cur) { runs.push(cur); cur = null; } continue; }
    const at = (t) => [ax + dx * t, ay + dy * t];
    if (!cur) cur = at(t0);
    else if (t0 > 1e-12) { runs.push(cur); cur = at(t0); }
    cur.push(...at(t1));
    if (t1 < 1 - 1e-12) { runs.push(cur); cur = null; }
  }
  if (cur) runs.push(cur);
  return runs;
}

/** Chaikin corner cutting: rounds a polyline, keeping both end points where they are. */
export function chaikin(pts, iterations = 1) {
  let p = pts;
  for (let k = 0; k < iterations; k++) {
    const n = p.length / 2;
    if (n < 3) return p;
    const out = [p[0], p[1]];
    for (let i = 0; i < n - 1; i++) {
      const ax = p[2 * i], ay = p[2 * i + 1], bx = p[2 * i + 2], by = p[2 * i + 3];
      out.push(0.75 * ax + 0.25 * bx, 0.75 * ay + 0.25 * by, 0.25 * ax + 0.75 * bx, 0.25 * ay + 0.75 * by);
    }
    out.push(p[2 * n - 2], p[2 * n - 1]);
    p = out;
  }
  return p;
}

/** Douglas-Peucker: drop points that are within tol metres of the straight line between their neighbours. */
export function simplifyDP(pts, tol) {
  const n = pts.length / 2;
  if (n < 3) return pts;
  const keep = new Uint8Array(n);
  keep[0] = keep[n - 1] = 1;
  const stack = [[0, n - 1]];
  while (stack.length) {
    const [a, b] = stack.pop();
    let best = -1, bd = tol;
    for (let i = a + 1; i < b; i++) {
      const d = closestOnSeg(pts[2 * i], pts[2 * i + 1], pts[2 * a], pts[2 * a + 1], pts[2 * b], pts[2 * b + 1]).d;
      if (d > bd) { bd = d; best = i; }
    }
    if (best >= 0) { keep[best] = 1; stack.push([a, best], [best, b]); }
  }
  const out = [];
  for (let i = 0; i < n; i++) if (keep[i]) out.push(pts[2 * i], pts[2 * i + 1]);
  return out;
}
