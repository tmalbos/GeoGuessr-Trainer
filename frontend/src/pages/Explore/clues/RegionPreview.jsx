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
export function areaNameSpot(geom, name, project, px, sig, image = false) {
  let seen = SPOTS.get(geom);
  if (!seen) { seen = new Map(); SPOTS.set(geom, seen); }
  const key = `${image ? "\0img" : name}|${px.toFixed(2)}|${sig}`;
  if (!seen.has(key)) {
    if (seen.size > 60) seen.clear();
    seen.set(key, image ? thumbSpot(geom, project, px) : nameSpot(geom, name, project, px));
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

// ── Labels that don't fit on their area ──
// An area with an image shows it as a miniature (THUMB_PX wide) instead of its name. When a label doesn't fit on its
// area (whole map in view only), a dot inside the area is joined by a line to the label, which goes in the empty space
// beside the country: a left or right column, stacked top to bottom without overlapping.
const THUMB_PX = 36;
const COL_SHARE = 0.2; // width of each label column, as a share of the map width
const GAP_SHARE = 0.02; // space between the country and a column
const STACK_GAP = 6;

function thumbSpot(geom, project, t) {
  for (const part of pieces(geom, project)) {
    const hit = bestIn(part, t, t / 2);
    if (hit) return { x: hit.x, y: hit.y, size: t };
  }
  return null;
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

// Left and right edge of a country outline in map units (null when it has no polygons).
function xRangeOf(g, project) {
  const polys = g.type === "MultiPolygon" ? g.coordinates : g.type === "Polygon" ? [g.coordinates] : [];
  let lo = Infinity;
  let hi = -Infinity;
  for (const p of polys) for (const [x] of p[0].map(project)) { lo = Math.min(lo, x); hi = Math.max(hi, x); }
  return Number.isFinite(lo) ? [lo, hi] : null;
}

function wrapName(name, f, maxW) {
  const per = Math.max(1, Math.floor(maxW / (f * CHAR_W)));
  const lines = [];
  let cur = "";
  for (const word of name.split(" ")) {
    if (cur && cur.length + 1 + word.length > per) { lines.push(cur); cur = word; }
    else cur = cur ? `${cur} ${word}` : word;
  }
  lines.push(cur);
  return { lines, size: Math.min(f, maxW / (Math.max(...lines.map((t) => t.length)) * CHAR_W)) };
}

/** groups = [[name, geometry, image | null]]. -> { on: labels sitting on their area, calls: labels in a side column
 *  (with the dot `at` and the end of the line), x0 / x1: the horizontal extent the view needs to show the columns }.
 *  Without `callouts` the labels that don't fit are left out. */
function layoutLabels(groups, project, ext, W, H, scale, sig, callouts) {
  const u = 1 / scale;
  const colW = COL_SHARE * W;
  const gap = GAP_SHARE * W;
  const thumb = Math.min(THUMB_PX * u, colW);
  const on = [];
  const sides = { L: [], R: [] };
  for (const [name, geom, image] of groups) {
    if (!name) continue;
    const spot = areaNameSpot(geom, name, project, image ? thumb : NAME_PX * u, sig, !!image);
    if (spot) { on.push({ name, image, spot }); continue; }
    const at = callouts ? anchorOf(geom, project) : null;
    if (!at) continue;
    let item = { name, image, at, bw: thumb, bh: thumb };
    if (!image) {
      const { lines, size } = wrapName(name, NAME_PX * u, colW);
      item = { ...item, lines, size, bw: Math.max(...lines.map((t) => t.length)) * size * CHAR_W + size * 0.3, bh: lines.length * size * 1.1 };
    }
    sides[at[0] < (ext[0] + ext[1]) / 2 ? "L" : "R"].push(item);
  }
  const calls = [];
  for (const side of ["L", "R"]) {
    const items = sides[side].sort((a, b) => a.at[1] - b.at[1]);
    let prev = -STACK_GAP;
    for (const it of items) { it.y0 = Math.max(it.at[1] - it.bh / 2, prev + STACK_GAP); prev = it.y0 + it.bh; }
    let limit = H;
    for (let i = items.length - 1; i >= 0; i--) {
      if (items[i].y0 + items[i].bh > limit) items[i].y0 = limit - items[i].bh;
      limit = items[i].y0 - STACK_GAP;
    }
    const cx = side === "L" ? ext[0] - gap - colW / 2 : ext[1] + gap + colW / 2;
    for (const it of items) {
      if (it.y0 < -0.5) continue; // no room left in the column
      const cy = it.y0 + it.bh / 2;
      calls.push({
        name: it.name, image: it.image, side, at: it.at,
        end: [side === "L" ? cx + it.bw / 2 + 4 : cx - it.bw / 2 - 4, cy],
        spot: it.image ? { x: cx, y: cy, size: it.bw } : { x: cx, y: cy, size: it.size, lines: it.lines },
      });
    }
  }
  const used = (s) => calls.some((c) => c.side === s);
  return {
    on, calls,
    x0: used("L") ? Math.min(0, ext[0] - gap - colW) : 0,
    x1: used("R") ? Math.max(W, ext[1] + gap + colW) : W,
  };
}

/** The miniature of an area's image, centred on its spot (spot.size = side of the square). */
export function AreaThumb({ spot, href }) {
  const t = spot.size;
  const x = (spot.x - t / 2).toFixed(1);
  const y = (spot.y - t / 2).toFixed(1);
  return <g style={{ pointerEvents: "none" }}>
    <rect x={x} y={y} width={t.toFixed(1)} height={t.toFixed(1)} rx={t * 0.08} fill="#0d1117" stroke="#fff" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    <image href={href} x={x} y={y} width={t.toFixed(1)} height={t.toFixed(1)} preserveAspectRatio="xMidYMid meet" />
  </g>;
}

/** A label in a side column: dot on the area, line out of the country, then the name or the miniature. `u` = map units per screen px. */
function Callout({ c, u }) {
  const d = `M${c.at[0].toFixed(1)} ${c.at[1].toFixed(1)}L${c.end[0].toFixed(1)} ${c.end[1].toFixed(1)}`;
  return <g style={{ pointerEvents: "none" }}>
    <path d={d} fill="none" stroke="#fff" strokeWidth="3.5" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    <path d={d} fill="none" stroke="#0d1117" strokeWidth="1.5" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    <circle cx={c.at[0].toFixed(1)} cy={c.at[1].toFixed(1)} r={(3.5 * u).toFixed(2)} fill="#0d1117" stroke="#fff" strokeWidth="1.5" vectorEffect="non-scaling-stroke" />
    {c.image ? <AreaThumb spot={c.spot} href={c.image} /> : <AreaName spot={c.spot} />}
  </g>;
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
  // The label columns change the viewBox without resizing the box: read the scale again after every render.
  useEffect(() => {
    const r = svgEl?.getBoundingClientRect();
    const vb = svgEl?.viewBox.baseVal;
    if (r?.width && vb?.width) setScale(Math.round(Math.min(r.width / vb.width, r.height / vb.height) * 1000) / 1000);
  });
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
  // Names / miniatures: on the area when they fit; with the whole map in view the rest go in the empty space beside the country.
  const flat = zk === 1 && vh === H;
  const lay = showNames && scale > 0 && areaLayers.length
    ? layoutLabels(areaLayers.flatMap(groupsOf), project, (outline && xRangeOf(outline, project)) || [0, W], W, H, scale, `${bb.join()}|${W}|${H}|${align}|${projection}`, flat)
    : null;
  const bx = lay && flat ? lay.x0 : vx;
  const bw = lay && flat ? lay.x1 - lay.x0 : vw;
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

  return <svg ref={setSvgEl} className="cl-svg" style={{ ...style, "--stain": stainColor ?? undefined, touchAction: zk > 1 ? "none" : undefined }} viewBox={`${bx} ${vy} ${bw} ${vh}`} role="img" aria-label={label}
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
    {lay && lay.on.map((l, i) => (l.image
      ? <AreaThumb key={`name-${l.name}-${i}`} spot={l.spot} href={l.image} />
      : <AreaName key={`name-${l.name}-${i}`} spot={l.spot} />))}
    {lay && lay.calls.map((c, i) => <Callout key={`call-${c.name}-${i}`} c={c} u={1 / scale} />)}
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
