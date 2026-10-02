import { useEffect, useState } from "react";
import { loadCountryMap } from "../geo.js";
import { alignedProjection, bboxOf, fitProjection, shapes } from "./geoDraw.js";

const GEO_KINDS = ["polygons", "lines", "points"];
const norm = (s) => s.trim().toLowerCase();

// CSV row = GroupName, Level1, Level2, ... Only Level1 is filled -> the whole region applies ("full").
// Deeper levels filled -> only part of the region applies ("part", lighter).
function csvLevels(rows) {
  const m = new Map();
  for (const r of rows.slice(1)) {
    const name = norm(r[1] ?? "");
    if (!name) continue;
    if (!r.slice(2).some((c) => c.trim())) m.set(name, "full");
    else if (m.get(name) !== "full") m.set(name, "part");
  }
  return m;
}

// The country outline with the areas a clue applies to painted on it.
//
// ASSUMPTION: geometry is overlaid using `bbox` ([minLon, minLat, maxLon, maxLat]) from
// /geo/admin1/{id}.json, assuming the map is a linear (equirectangular) fit of that bbox
// into data.w × data.h. Without `bbox`, polygons/lines/points are drawn alone, without outline.
export default function ClueMap({ cc, clue }) {
  const [map, setMap] = useState(null);
  const [geo, setGeo] = useState({});
  const [err, setErr] = useState("");
  const kinds = GEO_KINDS.filter((k) => clue[k]);

  useEffect(() => {
    let off = false;
    setMap(null); setGeo({}); setErr("");
    loadCountryMap(cc).then((d) => !off && setMap(d)).catch((e) => !off && setErr(e.message));
    for (const k of kinds) {
      fetch(`/api/clues/${cc}/geo/${clue[k]}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${clue[k]}: HTTP ${r.status}`))))
        .then((g) => !off && setGeo((p) => ({ ...p, [k]: g })))
        .catch((e) => !off && setErr(e.message));
    }
    return () => { off = true; };
  }, [cc, clue.id]);

  if (err) return <p className="err">{err}</p>;
  if (!map || !kinds.every((k) => geo[k])) return <p className="muted">Loading map…</p>;

  const layers = kinds.map((k) => geo[k]);
  const aligned = !!map.bbox;
  const standalone = layers.length > 0 && !aligned;
  const levels = clue.csv_rows ? csvLevels(clue.csv_rows) : new Map();
  const pad = 8;

  let W = map.w, H = map.h, project = null;
  if (standalone) {
    const bb = bboxOf({ type: "GeometryCollection", geometries: layers });
    W = 1000; H = 700;
    project = fitProjection(bb, W, H);
  } else if (layers.length) {
    project = alignedProjection(map.bbox, W, H);
  }
  const drawn = project ? layers.map((g) => shapes(g, project)) : [];

  return <svg className="cl-svg" viewBox={`${-pad} ${-pad} ${W + pad * 2} ${H + pad * 2}`} role="img"
    aria-label={`Map of ${map.name} showing where this clue applies`}>
    {!standalone && map.regions.map((r) => {
      const cls = `cl-r ${levels.get(norm(r.name)) ?? ""}`;
      return <g key={r.name}>
        <path className={cls} d={r.d} fillRule="evenodd" />
        {(r.dots ?? []).map(([x, y], i) => <circle key={i} className={cls} cx={x} cy={y} r="11" />)}
      </g>;
    })}
    {drawn.map((s, i) => <g key={i}>
      <path className="cl-poly" d={s.polys} fillRule="evenodd" />
      <path className="cl-line" d={s.lines} fill="none" />
      {s.pts.map(([x, y], j) => <circle key={j} className="cl-pt" cx={x} cy={y} r="5" />)}
    </g>)}
  </svg>;
}
