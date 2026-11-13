import { useEffect, useState } from "react";
import { api } from "../../../lib/api.js";
import { bboxOf, fitProjection, shadeFactor, shapes } from "./geoDraw.js";
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
const darken = (hex) => `#${[1, 3, 5].map((i) => Math.round(parseInt(hex.slice(i, i + 2), 16) * 0.55).toString(16).padStart(2, "0")).join("")}`;
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

/** A layer -> { rest: the layer without its small polygons, pins: [{ at: [x, y], shade }] }. */
function splitPins(g, total, project) {
  if (g.type !== "GeometryCollection" || !total) return { rest: g, pins: [] };
  const small = g.geometries.map((x, i) => (isSmall(x, total, project) ? i : -1)).filter((i) => i >= 0);
  if (!small.length || small.length > MAX_PINS) return { rest: g, pins: [] };
  const gone = new Set(small);
  const keep = (_, i) => !gone.has(i);
  const rest = { ...g, geometries: g.geometries.filter(keep) };
  if (Array.isArray(g.shades)) rest.shades = g.shades.filter(keep);
  return { rest, pins: small.map((i) => ({ at: centre(g.geometries[i], project), shade: g.shades?.[i] ?? 0 })) };
}

// ── Area names ──
// A name is printed on its area only where it fits: NAME_PX is the text height on screen. It is tried at full size,
// then split in two lines (names with spaces), then a bit smaller, on any of the area's biggest pieces, and the text
// box must lie inside that piece's own shape. Anywhere else the name is still there as the hover tooltip, and
// clicking the area shows it under the clue text.
export const NAME_PX = 12;
const NAME_SCALES = [1, 0.85, 0.7];
const CHAR_W = 0.58; // width of a bold character, in text heights
const MAX_PIECES = 4;
const SPOT_STEPS = 20;

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
      return { rings, top, bottom, width: right - left };
    });
}

/** Where to print `name` on an area, in map units: { x, y, size, lines } (the text is centred on x, y), or null when it
 *  doesn't fit anywhere. `px` = text size in map units. For each way of writing it (big to small, one line then two)
 *  every height of every piece is tried; the box must be inside the shape, and the roomiest, most central spot wins. */
export function nameSpot(geom, name, project, px) {
  const parts = pieces(geom, project);
  const two = splitName(name);
  for (const k of NAME_SCALES) {
    const size = px * k;
    for (const lines of two ? [[name], two] : [[name]]) {
      const w = Math.max(...lines.map((t) => t.length)) * size * CHAR_W + size * 0.3;
      const hh = size * (0.4 + 0.55 * (lines.length - 1));
      let best = null;
      for (const { rings, top, bottom, width } of parts) {
        if (width < w || bottom - top < hh * 2) continue;
        for (let s = 1; s < SPOT_STEPS; s++) {
          const y = top + ((bottom - top) * s) / SPOT_STEPS;
          for (const [a, b] of commonSpans(rings, [y - hh, y - hh / 2, y, y + hh / 2, y + hh])) {
            if (b - a < w) continue;
            const score = Math.min((b - a) / w, 2) - (0.3 * Math.abs(y - (top + bottom) / 2)) / (bottom - top);
            if (!best || score > best.score) best = { score, x: (a + b) / 2, y };
          }
        }
      }
      if (best) return { x: best.x, y: best.y, size, lines };
    }
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

/** The name written at its spot (one <tspan> per line). */
export function AreaName({ spot }) {
  const x = spot.x.toFixed(1);
  const gap = spot.size * 1.1;
  return <text className="cl-area-name" x={x} y={spot.y.toFixed(1)} fontSize={spot.size} strokeWidth={spot.size * 0.3}>
    {spot.lines.map((t, i) => <tspan key={i} x={x} dy={i === 0 ? (-(spot.lines.length - 1) * gap) / 2 : gap}>{t}</tspan>)}
  </text>;
}

// Pin drawn with its tip at (0, 0), about 46 map units tall. The big transparent circle makes it easy to click.
function Pin({ at: [x, y], className = "cl-poly", style, children, ...rest }) {
  return <g className={className} style={style} transform={`translate(${x.toFixed(1)} ${y.toFixed(1)}) scale(1.5)`} {...rest}>
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
export default function RegionPreview({ cc, layers, stains, onStain, onArea, selected, showNames, style, align, label = "Map of the country with the region painted on it" }) {
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
  }, [svgEl]);
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
  const k = Math.cos((((bb[1] + bb[3]) / 2) * Math.PI) / 180) || 1;
  const ratio = (bb[3] - bb[1]) / Math.max((bb[2] - bb[0]) * k, 1e-6);
  const H = Math.round(Math.min(1400, Math.max(200, W * ratio)));
  // A tall country is narrower than the viewBox: fitProjection would centre it, so right-align it there
  // (and preserveAspectRatio below does the same when the box itself is wider than the drawing).
  const project = fitProjection(bb, W, H, 6, align === "right");
  const land = outline ? shapes(outline, project).polys : "";
  const borders = base?.borders ? shapes(base.borders, project).lines : "";

  const total = outline ? polyArea(outline, project) : 0;
  const plain = layers.filter((g) => !g.colors);
  const areaLayers = layers.filter((g) => g.colors); // named, colored areas (see areas.css)
  const split = plain.map((g) => splitPins(g, total, project));
  const smallStains = (stains ?? []).filter((s) => isSmall(s.geometry, total, project));
  const pinned = new Set(smallStains.length <= MAX_PINS ? smallStains.map((s) => s.id) : []);
  const stainTop = Math.max(0, ...(stains ?? []).map((s) => s.shade ?? 0));

  return <svg ref={setSvgEl} className="cl-svg" style={{ ...style, "--stain": stainColor ?? undefined, "--stain-dark": stainColor ? darken(stainColor) : undefined }} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}
    preserveAspectRatio={align === "right" ? "xMaxYMid meet" : undefined}>
    {land && <path className="cl-r" d={land} fillRule="evenodd" />}
    {borders && <path d={borders} fill="none" stroke="#8b98a8" strokeWidth=".8" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
    {split.map(({ rest, pins }, i) => {
      const s = shapes(rest, project);
      const parts = tiers(rest);
      const top = parts ? Math.max(...parts.map(([v]) => v)) : 0;
      const pinTop = Math.max(0, ...pins.map((p) => p.shade));
      return <g key={i}>
        {parts
          ? parts.map(([v, part]) => {
            const op = 0.85 * shadeFactor(v, top);
            return <path key={v} className="cl-poly" d={shapes(part, project).polys} fillRule="evenodd"
              style={v ? { fillOpacity: op, strokeOpacity: op } : undefined} />;
          })
          : <path className="cl-poly" d={s.polys} fillRule="evenodd" />}
        <path className="cl-line" d={s.lines} fill="none" />
        {s.pts.map(([x, y], j) => <circle key={j} className="cl-pt" cx={x} cy={y} r="5" />)}
        {pins.map((p, j) => {
          const op = p.shade ? 0.85 * shadeFactor(p.shade, pinTop) : null;
          return <Pin key={`pin-${j}`} at={p.at} style={op != null ? { fillOpacity: op, strokeOpacity: op } : undefined} />;
        })}
      </g>;
    })}
    {areaLayers.flatMap((g) => g.geometries.map((geom, i) => {
      const name = g.names?.[i] ?? "";
      const extra = g.extras?.[i] ?? "";
      const click = !!onArea && !!(name || extra);
      const act = click ? {
        role: "button", tabIndex: 0, "aria-label": name,
        onClick: () => onArea({ name, extra }),
        onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onArea({ name, extra }); } },
      } : {};
      return <path key={`${name}-${i}`} className={`cl-area ${click ? "click" : ""} ${selected === name ? "sel" : ""}`}
        onMouseEnter={click ? () => setHoveredArea(name) : undefined}
        onMouseLeave={click ? () => setHoveredArea(null) : undefined}
        d={shapes(geom, project).polys} fillRule="evenodd" style={{ fill: g.colors[i] }} {...act}><title>{name}</title></path>;
    }))}
    {selected && areaLayers.flatMap((g) => g.geometries.map((geom, i) => (g.names?.[i] === selected
      ? <path key={`ring-${selected}-${i}`} className="cl-area-ring" d={shapes(geom, project).polys} fillRule="evenodd" /> : null)))}
    {hoveredArea && hoveredArea !== selected && areaLayers.flatMap((g) => g.geometries.map((geom, i) => (g.names?.[i] === hoveredArea
      ? <path key={`hover-${hoveredArea}-${i}`} className="cl-area-ring" d={shapes(geom, project).polys} fillRule="evenodd" /> : null)))}
    {showNames && scale > 0 && areaLayers.flatMap((g) => g.geometries.map((geom, i) => {
      const name = g.names?.[i];
      const spot = name ? areaNameSpot(geom, name, project, NAME_PX / scale, `${bb.join()}|${W}|${H}|${align}`) : null;
      return spot && <AreaName key={`name-${name}-${i}`} spot={spot} />;
    }))}
    {(stains ?? []).map((s) => {
      const toggle = () => onStain?.(s.id);
      const name = s.label ?? s.id;
      const cls = `cl-poly cl-stain ${s.active ? "" : "off"}`;
      const tone = stainColor && s.active ? lighten(stainColor, 1 - shadeFactor(s.shade ?? 0, stainTop)) : null;
      const paint = tone ? { fill: tone, stroke: darken(tone) } : null;
      const st = paint ?? undefined;
      const act = onStain ? {
        role: "button", tabIndex: 0, "aria-pressed": s.active, "aria-label": `${name}: ${s.active ? "included" : "left out"}`,
        onClick: toggle,
        onKeyDown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } },
      } : {};
      const tip = onStain && <title>{name} · click to {s.active ? "leave it out" : "include it"}</title>;
      if (pinned.has(s.id)) return <Pin key={s.id} at={centre(s.geometry, project)} className={cls} style={st} {...act}>{tip}</Pin>;
      return <path key={s.id} className={cls} d={shapes(s.geometry, project).polys} fillRule="evenodd" style={st} {...act}>{tip}</path>;
    })}
  </svg>;
}
