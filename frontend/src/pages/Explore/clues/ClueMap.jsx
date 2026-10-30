import { useEffect, useState } from "react";
import { loadCountryMap } from "../geo.js";
import RegionPreview from "./RegionPreview.jsx";

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

// The country with the areas a clue applies to painted on it.
// - Geometry files (polygons / lines / points): drawn on the country's own outline and borders (RegionPreview).
// - CSV: the country's first-level subdivisions, painted.
export default function ClueMap({ cc, clue, align }) {
  const [map, setMap] = useState(null);
  const [geo, setGeo] = useState({});
  const [err, setErr] = useState("");
  const kinds = GEO_KINDS.filter((k) => clue[k]);
  const hasGeo = kinds.length > 0;

  useEffect(() => {
    let off = false;
    setMap(null); setGeo({}); setErr("");
    if (!hasGeo) loadCountryMap(cc).then((d) => !off && setMap(d)).catch((e) => !off && setErr(e.message));
    for (const k of kinds) {
      fetch(`/api/clues/${cc}/geo/${clue[k]}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${clue[k]}: HTTP ${r.status}`))))
        .then((g) => !off && setGeo((p) => ({ ...p, [k]: g })))
        .catch((e) => !off && setErr(e.message));
    }
    return () => { off = true; };
  }, [cc, clue.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (err) return <p className="err">{err}</p>;

  if (hasGeo) {
    if (!kinds.every((k) => geo[k])) return <p className="muted">Loading map…</p>;
    return <RegionPreview cc={cc} layers={kinds.map((k) => geo[k])} align={align} />;
  }

  if (!map) return <p className="muted">Loading map…</p>;
  const levels = clue.csv_rows ? csvLevels(clue.csv_rows) : new Map();
  const pad = 8;
  return <svg className="cl-svg" viewBox={`${-pad} ${-pad} ${map.w + pad * 2} ${map.h + pad * 2}`} role="img"
    aria-label={`Map of ${map.name} showing where this clue applies`}>
    {map.regions.map((r) => {
      const cls = `cl-r ${levels.get(norm(r.name)) ?? ""}`;
      return <g key={r.name}>
        <path className={cls} d={r.d} fillRule="evenodd" />
        {(r.dots ?? []).map(([x, y], i) => <circle key={i} className={cls} cx={x} cy={y} r="11" />)}
      </g>;
    })}
  </svg>;
}
