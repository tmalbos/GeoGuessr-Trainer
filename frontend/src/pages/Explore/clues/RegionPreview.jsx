import { useEffect, useState } from "react";
import { api } from "../../../lib/api.js";
import { bboxOf, shadeFactor, shapes } from "./geoDraw.js";
import { makeProjection } from "./projections.js";
import "./import-extras.css";
import "./areas.css";

// A region (polygons / lines / points, lon/lat GeoJSON) drawn ON its country: the country's outline and
// first-level borders come from the backend (/clues/{cc}/base), so the region is never shown floating alone.
const cache = new Map();
const loadBase = (cc) => {
  const k = cc.toUpperCase();
  if (!cache.has(k)) cache.set(k, api(`/clues/${k}/base`).catch((e) => { cache.delete(k); throw e; }));
  return cache.get(k);
};

// Regional stains are painted in the colour picked in Settings (default: red).
const loadStainColor = () => api("/settings").then((s) => s.stain_color || "#c8283a").catch(() => "#c8283a");
const lighten = (hex, k) => `#${[1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) + (255 - parseInt(hex.slice(i, i + 2), 16)) * k).toString(16).padStart(2, "0")).join("")}`;

// A layer may carry `shades` (one number per geometry, 0 = the main tone, higher = rarer = lighter).
// Returns [[shade, geometry collection]], lightest first so the stronger tones sit on top.
const tiers = (g) => {
  const sh = g.shades;
  if (!Array.isArray(sh) || !sh.some((v) => v > 0)) return null;
  return [...new Set(sh)].sort((a, b) => b - a)
    .map((v) => [v, { type: "GeometryCollection", geometries: g.geometries.filter((_, i) => sh[i] === v) }]);
};

// ── Pins ──
// A polygon smaller than 0.05% of the country is too small to see, so it is DRAWN as a pin whose tip sits at the
// polygon's centre. Only the drawing changes: the stored geometry is untouched. If more than MAX_PINS polygons would qualify, it is a
// scattered region (many small stains), not a handful of pinpoints, so they are all drawn as shapes.
const MAX_PIN_SHARE = 0.001;
const MAX_PINS = 12;
const FOCUS_K = 4;

const ringArea = (ring, project) => {
  const p = ring.map(project);
  let a = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i];
    const [x2, y2] = p[(i + 1) % p.length];
    a += x1 * y2 - x2 * y1;
  }
  return Math.abs(a / 2);
};
const polyArea = (g, project) => {
  if (!g) return 0;
  if (g.type === "Polygon") return g.coordinates.reduce((s, r, i) => s + (i ? -1 : 1) * ringArea(r, project), 0);
  if (g.type === "MultiPolygon") return g.coordinates.reduce((s, c) => s + polyArea({ type: "Polygon", coordinates: c }, project), 0);
  if (g.type === "GeometryCollection") return g.geometries.reduce((s, x) => s + polyArea(x, project), 0);
  return 0;
};
const centre = (g, project) => {
  const p = g.coordinates[0].map(project);
  const xs = p.map((c) => c[0]);
  const ys = p.map((c) => c[1]);
  return [(Math.min(...xs) + Math.max(...xs)) / 2, (Math.min(...ys) + Math.max(...ys)) / 2];
};
const isSmall = (g, total, project) => g?.type === "Polygon" && total > 0 && polyArea(g, project) / total <= MAX_PIN_SHARE;

// A small polygon within NEAR_UNITS of a bigger one is drawn as a normal shape, not a pin (keep equal to NEAR_UNITS in src/geo/clue_overlay.py).
// Distance = gap between bounding boxes. `small` = indices of the small polygons in `geoms`; returns the ones that stay pins.
const NEAR_UNITS = 40;
const boxOf = (g, project) => {
  const p = g.coordinates[0].map(project);
  const xs = p.map((c) => c[0]);
  const ys = p.map((c) => c[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
};
function aloneSmall(geoms, small, project) {
  const gone = new Set(small);
  const bigger = geoms.map((g, i) => (gone.has(i) || g?.type !== "Polygon" ? null : boxOf(g, project))).filter(Boolean);
  if (!bigger.length) return small;
  return small.filter((i) => {
    const a = boxOf(geoms[i], project);
    return bigger.every((b) => Math.hypot(Math.max(b[0] - a[2], a[0] - b[2], 0), Math.max(b[1] - a[3], a[1] - b[3], 0)) > NEAR_UNITS);
  });
}

/** A layer -> { rest: the layer without its small polygons, pins: [{ at: [x, y], shade }] }. */
function splitPins(g, total, project) {
  if (g.type !== "GeometryCollection" || !total) return { rest: g, pins: [] };
  const tiny = g.geometries.map((x, i) => (isSmall(x, total, project) ? i : -1)).filter((i) => i >= 0);
  const small = aloneSmall(g.geometries, tiny, project);
  if (!small.length || small.length > MAX_PINS) return { rest: g, pins: [] };
  const gone = new Set(small);
  const keep = (_, i) => !gone.has(i);
  const rest = { ...g, geometries: g.geometries.filter(keep) };
  if (Array.isArray(g.shades)) rest.shades = g.shades.filter(keep);
  return { rest, pins: small.map((i) => ({ at: centre(g.geometries[i], project), shade: g.shades?.[i] ?? 0 })) };
}

// Pins that would overlap on screen become one pin in the middle of them (keep equal to _merge_pins in src/geo/clue_overlay.py).
const PIN_W = 30;
const PIN_TALL = 45;
function mergePins(pins, size) {
  const out = pins.map((p) => ({ ...p, n: 1 }));
  for (let merged = true; merged;) {
    merged = false;
    for (let i = 0; i < out.length && !merged; i++) {
      for (let j = i + 1; j < out.length && !merged; j++) {
        const a = out[i];
        const b = out[j];
        if (Math.abs(a.at[0] - b.at[0]) < PIN_W * size && Math.abs(a.at[1] - b.at[1]) < PIN_TALL * size) {
          const n = a.n + b.n;
          out[i] = { at: [(a.at[0] * a.n + b.at[0] * b.n) / n, (a.at[1] * a.n + b.at[1] * b.n) / n], shade: Math.min(a.shade, b.shade), n };
          out.splice(j, 1);
          merged = true;
        }
      }
    }
  }
  return out;
}

// ── Area names ──
// A name is printed on its area only where it fits: NAME_PX is the text height on screen. It is tried at full size,
// then split in two lines (names with spaces), at the spot closest to the centre of the area's biggest piece, and the text
// box must lie inside that piece's own shape. Anywhere else the name is still there as the hover tooltip, and
// clicking the area shows it under the clue text.
export const NAME_PX = 12;
const CHAR_W = 0.58; // width of a bold character, in text heights
const MAX_PIECES = 4;

// Horizontal stretches [x0, x1] that lie inside a polygon (all its rings, even-odd) at height y.
function spansAt(rings, y) {
  const xs = [];
  for (const r of rings) {
    for (let i = 0; i < r.length; i++) {
      const [x1, y1] = r[i];
      const [x2, y2] = r[(i + 1) % r.length];
      if ((y1 <= y) !== (y2 <= y)) xs.push(x1 + ((y - y1) / (y2 - y1)) * (x2 - x1));
    }
  }
  xs.sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i + 1 < xs.length; i += 2) out.push([xs[i], xs[i + 1]]);
  return out;
}

// Stretches of x that are inside the polygon at every one of the heights `ys`.
function commonSpans(rings, ys) {
  let acc = spansAt(rings, ys[0]);
  for (let k = 1; k < ys.length && acc.length; k++) {
    const next = spansAt(rings, ys[k]);
    const out = [];
    for (const [a, b] of acc) {
      for (const [c, d] of next) {
        const lo = Math.max(a, c);
        const hi = Math.min(b, d);
        if (hi > lo) out.push([lo, hi]);
      }
    }
    acc = out;
  }
  return acc;
}

// Two-line version of a name: split at the space closest to the middle (null when it has none).
function splitName(name) {
  let cut = -1;
  for (let i = 0; i < name.length; i++) {
    if (name[i] === " " && (cut < 0 || Math.abs(i - name.length / 2) < Math.abs(cut - name.length / 2))) cut = i;
  }
  return cut > 0 ? [name.slice(0, cut), name.slice(cut + 1)] : null;
}

// The biggest pieces of an area (lon/lat Polygon / MultiPolygon): projected rings and their extent.
function pieces(geom, project) {
  const polys = geom.type === "MultiPolygon" ? geom.coordinates : geom.type === "Polygon" ? [geom.coordinates] : [];
  return polys
    .map((p) => ({ p, a: ringArea(p[0], project) }))
    .sort((x, y) => y.a - x.a)
    .slice(0, MAX_PIECES)
    .map(({ p }) => {
      const rings = p.map((r) => r.map(project));
      let top = Infinity;
      let bottom = -Infinity;
      let left = Infinity;
      let right = -Infinity;
      for (const [x, y] of rings[0]) { top = Math.min(top, y); bottom = Math.max(bottom, y); left = Math.min(left, x); right = Math.max(right, x); }
      let a = 0;
      let cx = 0;
      let cy = 0;
      rings[0].forEach(([x1, y1], i) => {
        const [x2, y2] = rings[0][(i + 1) % rings[0].length];
        const f = x1 * y2 - x2 * y1;
        a += f;
        cx += (x1 + x2) * f;
        cy += (y1 + y2) * f;
      });
      const ok = Math.abs(a) > 1e-9;
      return { rings, top, bottom, width: right - left, cx: ok ? cx / (3 * a) : (left + right) / 2, cy: ok ? cy / (3 * a) : (top + bottom) / 2 };
    });
}

/** Closest spot to the centre of a piece where a w × 2hh text box lies inside its shape: { x, y, d } or null.
 *  Heights are tried outward from the centroid, so it stops as soon as nothing nearer can exist. */
function bestIn({ rings, top, bottom, width, cx, cy }, w, hh) {
  if (width < w || bottom - top < hh * 2) return null;
  const step = Math.max(hh / 3, (bottom - top) / 120);
  const reach = Math.max(cy - top, bottom - cy);
  let best = null;
  for (let k = 0; k * step <= reach; k++) {
    if (best && k * step >= best.d) break;
    for (const y of k ? [cy - k * step, cy + k * step] : [cy]) {
      if (y - hh < top || y + hh > bottom) continue;
      const rows = [-1, -2 / 3, -1 / 3, 0, 1 / 3, 2 / 3, 1].map((f) => y + f * hh);
      for (const [a, b] of commonSpans(rings, rows)) {
        if (b - a < w) continue;
        const x = Math.min(Math.max(cx, a + w / 2), b - w / 2);
        const d = Math.hypot(x - cx, y - cy);
        if (!best || d < best.d) best = { x, y, d };
      }
    }
  }
  return best;
}

/** Where to print `name` on an area, in map units: { x, y, size, lines } (the text is centred on x, y), or null when it
 *  doesn't fit anywhere. `px` = text size in map units, never reduced. The spot is the one closest to the centre of the
 *  area's biggest piece where the text box lies fully inside it (one line, or two when that is much closer). */
export function nameSpot(geom, name, project, px) {
  const two = splitName(name);
  for (const part of pieces(geom, project)) {
    let best = null;
    for (const lines of two ? [[name], two] : [[name]]) {
      const w = Math.max(...lines.map((t) => t.length)) * px * CHAR_W + px * 0.3;
      const hh = px * (0.4 + 0.55 * (lines.length - 1));
      const hit = bestIn(part, w, hh);
      const score = hit && hit.d + (lines.length - 1) * px;
      if (hit && (!best || score < best.score)) best = { score, x: hit.x, y: hit.y, size: px, lines };
    }
    if (best) return { x: best.x, y: best.y, size: best.size, lines: best.lines };
  }
  return null;
}

// nameSpot is the slow part and the map re-renders often: remember its answer per shape and size.
// `sig` says which projection `project` is.
const SPOTS = new WeakMap();
export function areaNameSpot(geom, name, project, px, sig) {
  let seen = SPOTS.get(geom);
  if (!seen) { seen = new Map(); SPOTS.set(geom, seen); }
  const key = `${name}|${px.toFixed(2)}|${sig}`;
  if (!seen.has(key)) {
    if (seen.size > 60) seen.clear();
    seen.set(key, nameSpot(geom, name, project, px));
  }
  return seen.get(key);
}

// One label per name: the polygons that share a name are one area, so they are joined before a spot is looked for.
const GROUPS = new WeakMap();
const groupsOf = (g) => {
  if (!GROUPS.has(g)) {
    const by = new Map();
    const img = new Map();
    g.geometries.forEach((geom, i) => {
      const name = g.names?.[i];
      const extra = g.extras?.[i];
      const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
      if (name && polys.length) by.set(name, [...(by.get(name) ?? []), ...polys]);
      if (name && !img.has(name) && typeof extra === "string" && extra.startsWith("data:image/")) img.set(name, extra);
    });
    GROUPS.set(g, [...by].map(([name, coordinates]) => [name, { type: "MultiPolygon", coordinates }, img.get(name) ?? null]));
  }
  return GROUPS.get(g);
};

/** The name written at its spot (one <tspan> per line). */
export function AreaName({ spot }) {
  const x = spot.x.toFixed(1);
  const gap = spot.size * 1.1;
  return <text className="cl-area-name" x={x} y={spot.y.toFixed(1)} fontSize={spot.size} strokeWidth={spot.size * 0.3}>
    {spot.lines.map((t, i) => <tspan key={i} x={x} dy={i === 0 ? (-(spot.lines.length - 1) * gap) / 2 : gap}>{t}</tspan>)}
  </text>;
}

// ── Labels of named areas ──
// An area with an image shows it as a miniature (its real proportions, as big as fits) instead
// of its name. A label that doesn't fit on its area goes in empty space INSIDE the country's bounding box (outside the
// country or on land nobody painted, never over a painted area): a dot on the area is joined by a line to the label. Lines never touch each other or another label. Where there
// is no such space, nothing is shown. Works on whatever part of the map is in view (zoomed or not).
const THUMB_SIDES = [640, 480, 360, 280, 220, 170, 130, 100, 80, 64, 50, 42, 36]; // px: longest side of a miniature, biggest first
const CALL_MAX_SIDE = 100; // a miniature in free space stays this small, so it doesn't take the room other labels need
const CELL_PX = 6; // resolution of the free-space search
const MAX_TESTS = 300; // spots tried (nearest first) before giving up on a label

// Natural size of every miniature, read once (the area's `extra` is a data URI).
const DIMS = new Map(); // uri -> [w, h], or null when it can't be read
const LOADING = new Set();
const SUBS = new Set();
function loadDims(uri) {
  if (DIMS.has(uri) || LOADING.has(uri)) return;
  LOADING.add(uri);
  const img = new Image();
  const done = (v) => { LOADING.delete(uri); DIMS.set(uri, v); SUBS.forEach((f) => f()); };
  img.onload = () => done([img.naturalWidth, img.naturalHeight]);
  img.onerror = () => done(null);
  img.src = uri;
}

const fitBox = ([w, h], side) => (w >= h ? [side, (side * h) / w] : [(side * w) / h, side]);

function areaThumbSpot(geom, project, bw, bh, sig) {
  let seen = SPOTS.get(geom);
  if (!seen) { seen = new Map(); SPOTS.set(geom, seen); }
  const key = `img|${bw.toFixed(1)}x${bh.toFixed(1)}|${sig}`;
  if (!seen.has(key)) {
    if (seen.size > 60) seen.clear();
    let spot = null;
    for (const part of pieces(geom, project)) {
      const hit = bestIn(part, bw, bh / 2);
      if (hit) { spot = { x: hit.x, y: hit.y, w: bw, h: bh }; break; }
    }
    seen.set(key, spot);
  }
  return seen.get(key);
}

// A point inside the area's biggest piece: its centre, or the middle of the widest stretch at that height.
function anchorOf(geom, project) {
  const part = pieces(geom, project)[0];
  if (!part) return null;
  const spans = spansAt(part.rings, part.cy);
  if (!spans.length || spans.some(([a, b]) => part.cx >= a && part.cx <= b)) return [part.cx, part.cy];
  const [a, b] = spans.reduce((m, s) => (s[1] - s[0] > m[1] - m[0] ? s : m));
  return [(a + b) / 2, part.cy];
}

const labelBox = (s) => {
  const w = s.lines ? Math.max(...s.lines.map((t) => t.length)) * s.size * CHAR_W + s.size * 0.3 : s.w;
  const h = s.lines ? s.lines.length * s.size * 1.1 : s.h;
  return { x0: s.x - w / 2, y0: s.y - h / 2, x1: s.x + w / 2, y1: s.y + h / 2 };
};

// The country outline as horizontal-scan data, so "which stretches of this row are land" is cheap. Built once per projection.
const OUTLINES = new WeakMap();
const BUCKET = 4;
function outlineIndex(outline, project, sig) {
  let by = OUTLINES.get(outline);
  if (!by) { by = new Map(); OUTLINES.set(outline, by); }
  if (by.has(sig)) return by.get(sig);
  if (by.size > 8) by.clear();
  const polys = outline.type === "MultiPolygon" ? outline.coordinates : outline.type === "Polygon" ? [outline.coordinates] : [];
  const edges = [];
  let x0 = Infinity;
  let x1 = -Infinity;
  let y0 = Infinity;
  let y1 = -Infinity;
  for (const p of polys) {
    for (const ring of p) {
      const q = ring.map(project);
      for (let i = 0; i < q.length; i++) {
        const a = q[i];
        const b = q[(i + 1) % q.length];
        x0 = Math.min(x0, a[0]); x1 = Math.max(x1, a[0]); y0 = Math.min(y0, a[1]); y1 = Math.max(y1, a[1]);
        if (a[1] !== b[1]) edges.push([a[0], a[1], b[0], b[1]]);
      }
    }
  }
  const lo = Math.floor(y0 / BUCKET);
  const buckets = Array.from({ length: Number.isFinite(y1) ? Math.floor(y1 / BUCKET) - lo + 1 : 0 }, () => []);
  edges.forEach((e, k) => {
    for (let i = Math.floor(Math.min(e[1], e[3]) / BUCKET) - lo; i <= Math.floor(Math.max(e[1], e[3]) / BUCKET) - lo; i++) buckets[i].push(k);
  });
  const index = { edges, buckets, lo, box: Number.isFinite(x0) ? { x0, y0, x1, y1 } : null };
  by.set(sig, index);
  return index;
}

// Stretches [a, b] of the row at height y that are inside the country (even-odd, so holes count).
function landSpans(ix, y) {
  const b = Math.floor(y / BUCKET) - ix.lo;
  if (b < 0 || b >= ix.buckets.length) return [];
  const xs = [];
  for (const k of ix.buckets[b]) {
    const [x1, y1, x2, y2] = ix.edges[k];
    if ((y1 <= y) !== (y2 <= y)) xs.push(x1 + ((y - y1) / (y2 - y1)) * (x2 - x1));
  }
  xs.sort((p, q) => p - q);
  const out = [];
  for (let i = 0; i + 1 < xs.length; i += 2) out.push([xs[i], xs[i + 1]]);
  return out;
}

// Grid over `region` (the view inside the country's bounding box): 1 = a painted area (grown by one cell) or already used.
// Land nobody painted is free, like the space outside the country.
function freeGrid(painted, region, cell) {
  const cols = Math.max(1, Math.ceil(region.w / cell));
  const rows = Math.max(1, Math.ceil(region.h / cell));
  const land = new Uint8Array(cols * rows);
  const mark = (x, y) => {
    const c = Math.floor((x - region.x) / cell);
    const r = Math.floor((y - region.y) / cell);
    if (c >= 0 && c < cols && r >= 0 && r < rows) land[r * cols + c] = 1;
  };
  for (const ix of painted) {
    for (let r = 0; r < rows; r++) {
      for (const [a, b] of landSpans(ix, region.y + (r + 0.5) * cell)) {
        const c1 = Math.min(cols - 1, Math.floor((b - region.x) / cell));
        for (let c = Math.max(0, Math.floor((a - region.x) / cell)); c <= c1; c++) land[r * cols + c] = 1;
      }
    }
    for (const [x1, y1, x2, y2] of ix.edges) { mark(x1, y1); mark(x2, y2); } // thin or tiny areas the rows can miss
  }
  const grid = { cols, rows, occ: new Uint8Array(cols * rows), sat: null };
  for (let r = 0; r < rows; r++) {
    for (let c = 0; c < cols; c++) {
      if (!land[r * cols + c]) continue;
      for (let j = Math.max(0, r - 1); j <= Math.min(rows - 1, r + 1); j++) for (let i = Math.max(0, c - 1); i <= Math.min(cols - 1, c + 1); i++) grid.occ[j * cols + i] = 1;
    }
  }
  rebuild(grid);
  return grid;
}

function rebuild(g) {
  const w = g.cols + 1;
  g.sat = new Int32Array(w * (g.rows + 1));
  for (let r = 0; r < g.rows; r++) {
    for (let c = 0; c < g.cols; c++) g.sat[(r + 1) * w + c + 1] = g.occ[r * g.cols + c] + g.sat[r * w + c + 1] + g.sat[(r + 1) * w + c] - g.sat[r * w + c];
  }
}

// Used cells in the block of columns [c0, c1) and rows [r0, r1).
const used = (g, c0, r0, c1, r1) => { const w = g.cols + 1; return g.sat[r1 * w + c1] - g.sat[r0 * w + c1] - g.sat[r1 * w + c0] + g.sat[r0 * w + c0]; };

// Where the segment a -> c first enters the box (null when it never does).
function entryPoint(a, c, box) {
  const dx = c[0] - a[0];
  const dy = c[1] - a[1];
  let t0 = 0;
  let t1 = 1;
  for (const [p, q] of [[-dx, a[0] - box.x0], [dx, box.x1 - a[0]], [-dy, a[1] - box.y0], [dy, box.y1 - a[1]]]) {
    if (p === 0) { if (q < 0) return null; continue; }
    const t = q / p;
    if (p < 0) { if (t > t1) return null; t0 = Math.max(t0, t); } else { if (t < t0) return null; t1 = Math.min(t1, t); }
  }
  return t0 > t1 ? null : [a[0] + dx * t0, a[1] + dy * t0];
}

const pointGap = (p, a, b) => {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const t = dx || dy ? Math.max(0, Math.min(1, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / (dx * dx + dy * dy))) : 0;
  return Math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy);
};
// Distance between two segments (0 when they cross).
function segGap(a, b, c, d) {
  const o = (p, q, r) => (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0]);
  const d1 = o(a, b, c);
  const d2 = o(a, b, d);
  const d3 = o(c, d, a);
  const d4 = o(c, d, b);
  if (((d1 > 0 && d2 < 0) || (d1 < 0 && d2 > 0)) && ((d3 > 0 && d4 < 0) || (d3 < 0 && d4 > 0))) return 0;
  return Math.min(pointGap(a, c, d), pointGap(b, c, d), pointGap(c, a, b), pointGap(d, a, b));
}

// Nearest free spot for a w × h label whose line from `at` crosses no other line or label, or null.
function placeIn(grid, region, cell, o, at, lines, rects, gap) {
  const cw = Math.ceil(o.w / cell) + 1;
  const ch = Math.ceil(o.h / cell) + 1;
  if (cw > grid.cols || ch > grid.rows) return null;
  const cand = [];
  for (let r = 0; r + ch <= grid.rows; r++) {
    for (let c = 0; c + cw <= grid.cols; c++) {
      if (used(grid, c, r, c + cw, r + ch)) continue;
      const cx = region.x + (c + cw / 2) * cell;
      const cy = region.y + (r + ch / 2) * cell;
      cand.push([Math.hypot(cx - at[0], cy - at[1]), cx, cy, c, r, cw, ch]);
    }
  }
  cand.sort((p, q) => p[0] - q[0]);
  for (let k = 0; k < Math.min(cand.length, MAX_TESTS); k++) {
    const [, cx, cy, c, r] = cand[k];
    const box = { x0: cx - o.w / 2, y0: cy - o.h / 2, x1: cx + o.w / 2, y1: cy + o.h / 2 };
    const end = entryPoint(at, [cx, cy], box);
    if (!end || lines.some(([a, b]) => segGap(at, end, a, b) < gap)) continue;
    if (rects.some((q) => entryPoint(at, end, { x0: q.x0 - gap, y0: q.y0 - gap, x1: q.x1 + gap, y1: q.y1 + gap }))) continue;
    return { cx, cy, end, box, c, r, cw, ch };
  }
  return null;
}

const nameOptions = (name, f) => {
  const two = splitName(name);
  return [[name], ...(two ? [two] : [])].map((lines) => ({
    lines, size: f, w: Math.max(...lines.map((t) => t.length)) * f * CHAR_W + f * 0.3, h: lines.length * f * 1.1,
  }));
};

/** groups = [[name, lon/lat geometry, image | null]]; view = { x, y, w, h } (the part of the map in view, map units);
 *  outline = the country (lon/lat geometry); scale = screen px per map unit.
 *  -> { on: labels sitting on their area, calls: labels placed in free space (with the dot `at` and the end of the line) }.
 *  An image that is still loading is left out until it is known. */
export function layoutLabels({ groups, project, outline, view, scale, sig }) {
  const u = 1 / scale;
  const on = [];
  const rects = [];
  const wanted = [];
  for (const [name, geom, image] of groups) {
    if (!name) continue;
    let dim = null;
    if (image) {
      dim = DIMS.get(image);
      if (dim === undefined) continue;
      if (dim && !(dim[0] > 0 && dim[1] > 0)) dim = null;
    }
    let spot = null;
    if (dim) {
      for (const side of THUMB_SIDES) {
        const [bw, bh] = fitBox(dim, side * u);
        spot = areaThumbSpot(geom, project, bw, bh, sig);
        if (spot) break;
      }
    } else {
      spot = areaNameSpot(geom, name, project, NAME_PX * u, sig);
    }
    const shown = dim ? image : null;
    if (spot) { on.push({ name, image: shown, spot }); rects.push(labelBox(spot)); continue; }
    const at = anchorOf(geom, project);
    if (at && at[0] >= view.x && at[0] <= view.x + view.w && at[1] >= view.y && at[1] <= view.y + view.h) wanted.push({ name, image: shown, dim, at });
  }
  const calls = [];
  if (wanted.length && outline) {
    const ix = outlineIndex(outline, project, sig);
    const bx = ix.box;
    const x0 = bx ? Math.max(view.x, bx.x0) : view.x;
    const y0 = bx ? Math.max(view.y, bx.y0) : view.y;
    const x1 = bx ? Math.min(view.x + view.w, bx.x1) : view.x + view.w;
    const y1 = bx ? Math.min(view.y + view.h, bx.y1) : view.y + view.h;
    if (x1 > x0 && y1 > y0) {
      const cell = CELL_PX * u;
      const region = { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
      const grid = freeGrid(groups.map(([, geom]) => outlineIndex(geom, project, sig)), region, cell);
      const lines = [];
      for (const w of wanted) {
        const options = w.dim ? THUMB_SIDES.filter((s) => s <= CALL_MAX_SIDE).map((s) => { const [bw, bh] = fitBox(w.dim, s * u); return { w: bw, h: bh }; }) : nameOptions(w.name, NAME_PX * u);
        for (const o of options) {
          const hit = placeIn(grid, region, cell, o, w.at, lines, rects, 4 * u);
          if (!hit) continue;
          for (let r = Math.max(0, hit.r - 1); r < Math.min(grid.rows, hit.r + hit.ch + 1); r++) {
            for (let c = Math.max(0, hit.c - 1); c < Math.min(grid.cols, hit.c + hit.cw + 1); c++) grid.occ[r * grid.cols + c] = 1;
          }
          rebuild(grid);
          lines.push([w.at, hit.end]);
          rects.push(hit.box);
          calls.push({
            name: w.name, image: w.image, at: w.at, end: hit.end,
            spot: w.dim ? { x: hit.cx, y: hit.cy, w: o.w, h: o.h } : { x: hit.cx, y: hit.cy, size: o.size, lines: o.lines },
          });
          break;
        }
      }
    }
  }
  return { on, calls };
}

/** Reads the size of the miniatures among `uris` (data URIs; anything else is ignored). Re-renders when one is known. */
export function useImageDims(uris) {
  const [ver, setVer] = useState(0);
  useEffect(() => {
    const bump = () => setVer((n) => n + 1);
    SUBS.add(bump);
    return () => SUBS.delete(bump);
  }, []);
  useEffect(() => { for (const uri of uris) if (uri.startsWith("data:image/")) loadDims(uri); });
  return ver;
}

/** The miniature of an area's image, centred on its spot (spot.w × spot.h: the image's own proportions). */
export function AreaThumb({ spot, href }) {
  const x = (spot.x - spot.w / 2).toFixed(1);
  const y = (spot.y - spot.h / 2).toFixed(1);
  const w = spot.w.toFixed(1);
  const h = spot.h.toFixed(1);
  return <g style={{ pointerEvents: "none" }}>
    <image href={href} x={x} y={y} width={w} height={h} preserveAspectRatio="none" />
    <rect x={x} y={y} width={w} height={h} fill="none" stroke="#fff" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
  </g>;
}

/** A label in free space: dot on the area, line to the label. `u` = map units per screen px. */
function Callout({ c, u }) {
  const d = `M${c.at[0].toFixed(1)} ${c.at[1].toFixed(1)}L${c.end[0].toFixed(1)} ${c.end[1].toFixed(1)}`;
  return <g style={{ pointerEvents: "none" }}>
    <path d={d} fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    <path d={d} fill="none" stroke="#0d1117" strokeWidth="1.5" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    <circle cx={c.at[0].toFixed(1)} cy={c.at[1].toFixed(1)} r={(3.5 * u).toFixed(2)} fill="#0d1117" stroke="#fff" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    {c.image ? <AreaThumb spot={c.spot} href={c.image} /> : <AreaName spot={c.spot} />}
  </g>;
}

/** Everything layoutLabels placed. */
export function LabelLayer({ lay, u }) {
  return <>
    {lay.on.map((l, i) => (l.image
      ? <AreaThumb key={`name-${l.name}-${i}`} spot={l.spot} href={l.image} />
      : <AreaName key={`name-${l.name}-${i}`} spot={l.spot} />))}
    {lay.calls.map((c, i) => <Callout key={`call-${c.name}-${i}`} c={c} u={u} />)}
  </>;
}

// Pin drawn with its tip at (0, 0), about 46 map units tall. The big transparent circle makes it easy to click.
function Pin({ at: [x, y], className = "cl-poly", style, children, size = 1.5, ...rest }) {
  return <g className={className} style={style} transform={`translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(${size})`} {...rest}>
    <circle cy="-26" r="24" fill="transparent" stroke="none" />
    <path d="M0 0C-3 -9 -15 -18 -15 -30A15 15 0 1 1 15 -30C15 -18 3 -9 0 0Z" vectorEffect="non-scaling-stroke" />
    <circle cy="-30" r="5.5" fill="#fff" stroke="none" />
    {children}
  </g>;
}

/** layers: array of GeoJSON geometries. If the country outline can't be loaded, the region is drawn alone.
 *  stains (optional): [{ id, label?, geometry, shade, active }] drawn one by one on top of the layers. Each stain is ONE
 *  polygon. With `onStain(id)` each one can be clicked (or Enter / Space) to switch it off and on; an inactive stain is
 *  drawn grey, faint and dashed. A stain smaller than 2% of the country is drawn as a pin (see above).
 *  align="right": the country is drawn against the right edge of the box (used by the overlay on a clue photo). */
export default function RegionPreview({ cc, layers, stains, onStain, onArea, selected, showNames, style, align, projection, zoom, onZoom, pan, focus, onUnfocus, label = "Map of the country with the region painted on it" }) {
  const [base, setBase] = useState(undefined); // undefined = loading, null = not available
  const [stainColor, setStainColor] = useState(null);
  const [svgEl, setSvgEl] = useState(null);
  const [scale, setScale] = useState(0); // screen px per map unit: names are sized and fitted in real px
  const [hoveredArea, setHoveredArea] = useState(null);
  useImageDims(layers.flatMap((g) => g.extras ?? []));
  useEffect(() => {
    let off = false;
    loadStainColor().then((c) => !off && setStainColor(c));
    return () => { off = true; };
  }, [cc]);
  useEffect(() => {
    if (!svgEl || typeof ResizeObserver !== "function") return undefined;
    const read = () => {
      const r = svgEl.getBoundingClientRect();
      const vb = svgEl.viewBox.baseVal;
      if (r.width && vb.width) setScale(Math.round(Math.min(r.width / vb.width, r.height / vb.height) * 1000) / 1000);
    };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(svgEl);
    return () => ro.disconnect();
  }, [svgEl, zoom?.k, focus]);
  useEffect(() => {
    let off = false;
    setBase(undefined);
    loadBase(cc).then((b) => !off && setBase(b)).catch(() => !off && setBase(null));
    return () => { off = true; };
  }, [cc]);

  if (base === undefined) return <p className="muted">Loading map…</p>;
  const outline = base?.outline ?? null;
  const bb = bboxOf({
    type: "GeometryCollection",
    geometries: [...(outline ? [outline] : []), ...layers, ...(stains ?? []).map((s) => s.geometry)],
  });
  if (!bb) return null;

  const W = 1000;
  // A tall country is narrower than the viewBox: makeProjection would centre it, so right-align it there
  // (and preserveAspectRatio below does the same when the box itself is wider than the drawing).
  const proj = makeProjection(projection, bb, align === "right" && !zoom && !focus,
    [...(outline ? [outline] : []), ...layers, ...(stains ?? []).map((s) => s.geometry)]);
  const H = proj.H;
  const project = proj.fwd;
  const land = outline ? shapes(outline, project).polys : "";
  const borders = base?.borders ? shapes(base.borders, project).lines : "";

  const total = outline ? polyArea(outline, project) : 0;
  const plain = layers.filter((g) => !g.colors);
  const areaLayers = layers.filter((g) => g.colors); // named, colored areas (see areas.css)
  const split = plain.map((g) => (focus ? { rest: g, pins: [] } : splitPins(g, total, project)));
  const stainGeoms = (stains ?? []).map((s) => s.geometry);
  const tinyStains = stainGeoms.map((g, i) => (isSmall(g, total, project) ? i : -1)).filter((i) => i >= 0);
  const smallStains = aloneSmall(stainGeoms, tinyStains, project).map((i) => stains[i]);
  const pinned = new Set(smallStains.length <= MAX_PINS ? smallStains.map((s) => s.id) : []);

  // Zoom (see ZoomableRegion): the view is a 1/k window of the map centred on (fx, fy) and kept inside it.
  // `pan` = a ref object shared with the wrapper: { drag, moved }. A drag never counts as a click on an area.
  const zv = focus ? { k: FOCUS_K, fx: project(focus)[0] / W, fy: project(focus)[1] / H } : zoom;
  const zk = zv?.k ?? 1;
  const vw = W / zk;
  const vh = H / (zv?.kh ?? zk);
  const vx = Math.min(Math.max((zv?.fx ?? 0.5) * W - vw / 2, 0), W - vw);
  const vy = Math.min(Math.max((zv?.fy ?? 0.5) * H - vh / 2, 0), H - vh);
  // Names / miniatures of the areas: on the area when they fit, otherwise in free space inside the country's bounding box.
  const lay = showNames && scale > 0 && areaLayers.length
    ? layoutLabels({ groups: areaLayers.flatMap(groupsOf), project, outline, view: { x: vx, y: vy, w: vw, h: vh }, scale, sig: `${bb.join()}|${W}|${H}|${align}|${projection}` })
    : null;
  const fire = (a) => { if (!pan?.current.moved) onArea?.(a); };
  const panDown = (e) => {
    if (!pan) return;
    pan.current.moved = false;
    if (e.button || zk <= 1) return;
    pan.current.drag = { x: e.clientX, y: e.clientY, vx, vy };
  };
  const panMove = (e) => {
    const d = pan?.current.drag;
    if (!d || !scale) return;
    const dx = e.clientX - d.x;
    const dy = e.clientY - d.y;
    if (!pan.current.moved && Math.hypot(dx, dy) < 4) return;
    pan.current.moved = true;
    const x = Math.min(Math.max(d.vx - dx / scale, 0), W - vw);
    const y = Math.min(Math.max(d.vy - dy / scale, 0), H - vh);
    onZoom?.({ k: zk, fx: (x + vw / 2) / W, fy: (y + vh / 2) / H });
  };
  const panEnd = () => { if (pan) pan.current.drag = null; };

  return <svg ref={setSvgEl} className="cl-svg" style={{ ...style, "--stain": stainColor ?? undefined, touchAction: zk > 1 ? "none" : undefined }} viewBox={`${vx} ${vy} ${vw} ${vh}`} role="img" aria-label={label}
    onPointerDown={panDown} onPointerMove={panMove} onPointerUp={panEnd} onPointerLeave={panEnd} onPointerCancel={panEnd}
    preserveAspectRatio={align === "right" ? "xMaxYMid meet" : undefined}>
    {land && <path className="cl-r" d={land} fillRule="evenodd" />}
    {borders && <path d={borders} fill="none" stroke="#8b98a8" strokeWidth=".8" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
    {split.map(({ rest, pins }, i) => {
      const s = shapes(rest, project);
      const parts = tiers(rest);
      return <g key={i}>
        {parts
          ? parts.map(([v, part]) => {
            const op = 0.85 * shadeFactor(v);
            return <path key={v} className={focus ? "cl-poly cl-back" : "cl-poly"} onClick={focus ? onUnfocus : undefined} d={shapes(part, project).polys} fillRule="evenodd"
              style={v ? { fillOpacity: op, strokeOpacity: op } : undefined} />;
          })
          : <path className={focus ? "cl-poly cl-back" : "cl-poly"} onClick={focus ? onUnfocus : undefined} d={s.polys} fillRule="evenodd" />}
        <path className="cl-line" d={s.lines} fill="none" />
        {s.pts.map(([x, y], j) => <circle key={j} className="cl-pt" cx={x} cy={y} r="5" />)}
        {mergePins(pins, 1.5 / zk).map((p, j) => {
          const op = p.shade ? 0.85 * shadeFactor(p.shade) : null;
          return <Pin key={`pin-${j}`} at={p.at} size={1.5 / zk} style={op != null ? { fillOpacity: op, strokeOpacity: op } : undefined} />;
        })}
      </g>;
    })}
    {areaLayers.flatMap((g) => g.geometries.map((geom, i) => {
      const name = g.names?.[i] ?? "";
      const extra = g.extras?.[i] ?? "";
      const click = !!onArea && !!(name || extra);
      const look = { blur: !!g.extra_blurs?.[i], maxH: g.extra_heights?.[i] || null };
      const act = click ? {
        role: "button", tabIndex: 0, "aria-label": name,
        onClick: () => fire({ name, extra, ...look }),
        onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire({ name, extra, ...look }); } },
      } : {};
      return <path key={`${name}-${i}`} className={`cl-area ${click ? "click" : ""} ${selected === name ? "sel" : ""}`}
        onMouseEnter={click ? () => setHoveredArea(name) : undefined}
        onMouseLeave={click ? () => setHoveredArea(null) : undefined}
        d={shapes(geom, project).polys} fillRule="evenodd" style={{ fill: g.colors[i] }} {...act}><title>{name}</title></path>;
    }))}
    {onArea && scale > 0 && areaLayers.flatMap((g) => g.geometries.map((geom, i) => {
      const name = g.names?.[i] ?? "";
      const extra = g.extras?.[i] ?? "";
      // An area only a few screen px wide gets an invisible, wider click target on top (CSS .cl-area-hit).
      if (!(name || extra) || Math.sqrt(polyArea(geom, project)) * scale >= 28) return null;
      return <path key={`hit-${name}-${i}`} className="cl-area cl-area-hit" d={shapes(geom, project).polys} fillRule="evenodd"
        onClick={() => fire({ name, extra, blur: !!g.extra_blurs?.[i], maxH: g.extra_heights?.[i] || null })}
        onMouseEnter={() => setHoveredArea(name)}
        onMouseLeave={() => setHoveredArea(null)}><title>{name}</title></path>;
    }))}
    {selected && areaLayers.flatMap((g) => g.geometries.map((geom, i) => (g.names?.[i] === selected
      ? <path key={`ring-${selected}-${i}`} className="cl-area-ring" d={shapes(geom, project).polys} fillRule="evenodd" /> : null)))}
    {hoveredArea && hoveredArea !== selected && areaLayers.flatMap((g) => g.geometries.map((geom, i) => (g.names?.[i] === hoveredArea
      ? <path key={`hover-${hoveredArea}-${i}`} className="cl-area-ring" d={shapes(geom, project).polys} fillRule="evenodd" /> : null)))}
    {lay && <LabelLayer lay={lay} u={1 / scale} />}
    {(stains ?? []).map((s) => {
      const toggle = () => onStain?.(s.id);
      const name = s.label ?? s.id;
      const cls = `cl-poly cl-stain ${s.active ? "" : "off"}`;
      const tone = stainColor && s.active ? lighten(stainColor, 1 - shadeFactor(s.shade ?? 0)) : null;
      const paint = tone ? { fill: tone, stroke: tone } : null;
      const st = paint ?? undefined;
      const act = onStain ? {
        role: "button", tabIndex: 0, "aria-pressed": s.active, "aria-label": `${name}: ${s.active ? "included" : "left out"}`,
        onClick: toggle,
        onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } },
      } : {};
      const tip = onStain && <title>{name} · click to {s.active ? "leave it out" : "include it"}</title>;
      if (pinned.has(s.id)) return <Pin key={s.id} at={centre(s.geometry, project)} size={1.5 / zk} className={cls} style={st} {...act}>{tip}</Pin>;
      return <path key={s.id} className={cls} d={shapes(s.geometry, project).polys} fillRule="evenodd" style={st} {...act}>{tip}</path>;
    })}
  </svg>;
}
