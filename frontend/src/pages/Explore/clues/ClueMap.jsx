import { useEffect, useState } from "react";
import { useMemo } from "react";
import { api } from "../../../lib/api.js";
import { loadCountryMap } from "../geo.js";
import RegionPreview from "./RegionPreview.jsx";
import ZoomableRegion from "./ZoomableRegion.jsx";
import fileUrl from "./fileUrl.js";
import { depthOptions, groupLayer } from "./prefixColors.js";
import "./overlay.css";

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
// - Saved geometry clues carry `clue.overlay`: a transparent picture of the region already drawn on its country
//   (made by the server when the clue is saved). It is just an <img>: nothing to download or draw.
// - Geometry files without an overlay (not built yet): drawn on the country's own outline and borders (RegionPreview).
// - CSV: the country's first-level subdivisions, painted.
// align="right": the map sits against the right edge of its box (overlay on a clue photo).
export default function ClueMap({ cc, clue, align, onArea, selected }) {
  const [map, setMap] = useState(null);
  const [geo, setGeo] = useState({});
  const [err, setErr] = useState("");
  const kinds = GEO_KINDS.filter((k) => clue[k]);
  const hasGeo = kinds.length > 0;
  const overlay = hasGeo && !clue.areas && !clue.view ? clue.overlay : null;

  useEffect(() => {
    let off = false;
    setMap(null); setGeo({}); setErr("");
    if (overlay) return () => { off = true; };
    if (!hasGeo) loadCountryMap(cc).then((d) => !off && setMap(d)).catch((e) => !off && setErr(e.message));
    for (const k of kinds) {
      fetch(`/api/clues/${cc}/geo/${clue[k]}${clue.rev ? `?v=${clue.rev}` : ""}`)
        .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`${clue[k]}: HTTP ${r.status}`))))
        .then((g) => !off && setGeo((p) => ({ ...p, [k]: g })))
        .catch((e) => !off && setErr(e.message));
    }
    return () => { off = true; };
  }, [cc, clue.id, clue.rev, overlay]); // eslint-disable-line react-hooks/exhaustive-deps

  // Prefix colouring (area codes...): how many characters of the names are shown. Saved on the clue; the lowest one by default.
  const names = geo.polygons?.names;
  const prefixed = !!clue.prefix_colors && !clue.image && !!names;
  const options = useMemo(() => (prefixed ? depthOptions(names) : []), [prefixed, names]);
  const [saved, setSaved] = useState(clue.depth ?? null);
  const depth = options.includes(saved) ? saved : options[0];
  const grouped = useMemo(() => (options.length ? groupLayer(geo.polygons, depth, options) : null), [options, depth, geo.polygons]);
  const pickDepth = (d) => {
    setSaved(d);
    if (clue.id) api(`/clues/${cc}/${clue.id}/depth`, { method: "PUT", body: { depth: d } }).catch(() => {});
  };

  if (overlay) {
    return <img className={`cl-overlay-img ${align === "right" ? "right" : ""}`} src={fileUrl(cc, overlay, clue.rev)}
      alt="Map showing where this clue applies" draggable={false} />;
  }

  if (err) return <p className="err">{err}</p>;

  if (hasGeo) {
    if (!kinds.every((k) => geo[k])) return <p className="muted">Loading map…</p>;
    const Preview = onArea && !clue.view ? ZoomableRegion : RegionPreview; // map-only clues with clickable areas get zoom, unless the view is locked
    // A locked view (drawn clues) is fixed: the map stays there (areas are still clickable, but no zoom / pan).
    return <>
      <Preview cc={cc} layers={kinds.map((k) => (k === "polygons" && grouped ? grouped : geo[k]))} align={align} onArea={onArea} selected={selected} showNames={clue.show_names !== false}
        {...(clue.view ? { zoom: clue.view } : {})} />
      {options.length > 1 && <div className="tabs" style={{ margin: "8px 0 0" }} role="group" aria-label="Characters of the names to show">
        <span className="muted">Depth</span>
        {options.map((d) => <button key={d} type="button" className={d === depth ? "on" : ""} aria-pressed={d === depth} onClick={() => pickDepth(d)}>{d}</button>)}
      </div>}
    </>;
  }

  if (!map) return <p className="muted">Loading map…</p>;
  const levels = clue.csv_rows ? csvLevels(clue.csv_rows) : new Map();
  const pad = 8;
  return <svg className="cl-svg" viewBox={`${-pad} ${-pad} ${map.w + pad * 2} ${map.h + pad * 2}`} role="img"
    preserveAspectRatio={align === "right" ? "xMaxYMid meet" : undefined}
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
