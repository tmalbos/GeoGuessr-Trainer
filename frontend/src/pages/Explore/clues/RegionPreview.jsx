import { useEffect, useState } from "react";
import { api } from "../../../lib/api.js";
import { bboxOf, fitProjection, shadeFactor, shapes } from "./geoDraw.js";
import "./import-extras.css";

// A region (polygons / lines / points, lon/lat GeoJSON) drawn ON its country: the country's outline and
// first-level borders come from the backend (/clues/{cc}/base), so the region is never shown floating alone.
const cache = new Map();
const loadBase = (cc) => {
  const k = cc.toUpperCase();
  if (!cache.has(k)) cache.set(k, api(`/clues/${k}/base`).catch((e) => { cache.delete(k); throw e; }));
  return cache.get(k);
};

// A layer may carry `shades` (one number per geometry, 0 = the main tone, higher = rarer = lighter).
// Returns [[shade, geometry collection]], lightest first so the stronger tones sit on top.
const tiers = (g) => {
  const sh = g.shades;
  if (!Array.isArray(sh) || !sh.some((v) => v > 0)) return null;
  return [...new Set(sh)].sort((a, b) => b - a)
    .map((v) => [v, { type: "GeometryCollection", geometries: g.geometries.filter((_, i) => sh[i] === v) }]);
};

// ── Pins ──
// A polygon smaller than 2% of the country is too small to see, so it is DRAWN as a pin whose tip sits at the
// polygon's centre. Only the drawing changes: the stored geometry is untouched. (A little slack on the 2%, since a
// pinpoint circle is built to be about that big.) If more than MAX_PINS polygons would qualify, it is a
// scattered region (many small stains), not a handful of pinpoints, so they are all drawn as shapes.
const MAX_PIN_SHARE = 0.0009;
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

// Pin drawn with its tip at (0, 0), about 46 map units tall. The big transparent circle makes it easy to click.
function Pin({ at: [x, y], className = "cl-poly", style, children, ...rest }) {
  return <g className={className} style={style} transform={`translate(${x.toFixed(1)} ${y.toFixed(1)})`} {...rest}>
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
export default function RegionPreview({ cc, layers, stains, onStain, style, align, label = "Map of the country with the region painted on it" }) {
  const [base, setBase] = useState(undefined); // undefined = loading, null = not available
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
  const stainTop = Math.max(0, ...(stains ?? []).filter((s) => s.active).map((s) => s.shade ?? 0));

  const total = outline ? polyArea(outline, project) : 0;
  const split = layers.map((g) => splitPins(g, total, project));
  const smallStains = (stains ?? []).filter((s) => isSmall(s.geometry, total, project));
  const pinned = new Set(smallStains.length <= MAX_PINS ? smallStains.map((s) => s.id) : []);

  return <svg className="cl-svg" style={style} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}
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
    {(stains ?? []).map((s) => {
      const op = s.active && s.shade ? 0.85 * shadeFactor(s.shade, stainTop) : null;
      const toggle = () => onStain?.(s.id);
      const name = s.label ?? s.id;
      const cls = `cl-poly cl-stain ${s.active ? "" : "off"}`;
      const st = op != null ? { fillOpacity: op, strokeOpacity: op } : undefined;
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
