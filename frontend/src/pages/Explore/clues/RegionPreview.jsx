import { useEffect, useState } from "react";
import { api } from "../../../lib/api.js";
import { bboxOf, fitProjection, shadeFactor, shapes } from "./geoDraw.js";

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

/** layers: array of GeoJSON geometries. If the country outline can't be loaded, the region is drawn alone. */
export default function RegionPreview({ cc, layers, style, label = "Map of the country with the region painted on it" }) {
  const [base, setBase] = useState(undefined); // undefined = loading, null = not available
  useEffect(() => {
    let off = false;
    setBase(undefined);
    loadBase(cc).then((b) => !off && setBase(b)).catch(() => !off && setBase(null));
    return () => { off = true; };
  }, [cc]);

  if (base === undefined) return <p className="muted">Loading map…</p>;
  const outline = base?.outline ?? null;
  const bb = bboxOf({ type: "GeometryCollection", geometries: [...(outline ? [outline] : []), ...layers] });
  if (!bb) return null;

  const W = 1000;
  const k = Math.cos((((bb[1] + bb[3]) / 2) * Math.PI) / 180) || 1;
  const ratio = (bb[3] - bb[1]) / Math.max((bb[2] - bb[0]) * k, 1e-6);
  const H = Math.round(Math.min(1400, Math.max(200, W * ratio)));
  const project = fitProjection(bb, W, H, 6);
  const land = outline ? shapes(outline, project).polys : "";
  const borders = base?.borders ? shapes(base.borders, project).lines : "";

  return <svg className="cl-svg" style={style} viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label}>
    {land && <path className="cl-r" d={land} fillRule="evenodd" />}
    {borders && <path d={borders} fill="none" stroke="#8b98a8" strokeWidth=".8" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />}
    {layers.map((g, i) => {
      const s = shapes(g, project);
      const parts = tiers(g);
      const top = parts ? Math.max(...parts.map(([v]) => v)) : 0;
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
      </g>;
    })}
  </svg>;
}
