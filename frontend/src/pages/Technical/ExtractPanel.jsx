import { useEffect, useMemo, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { download } from "./export.js";

// Each kind of data is one shortcut: which part of the file to read, what it must have, and (optionally) one
// property whose values you tick. `suggest` = values offered even when the file sample doesn't show them.
export const PRESETS = [
  { id: "roads", label: "Roads", layer: "lines", base: [{ key: "highway", values: [] }],
    pick: { key: "highway", label: "Road types", defaults: ["motorway", "trunk", "primary", "secondary"], suggest: ["motorway", "trunk", "primary", "secondary", "tertiary", "unclassified", "residential"] },
    fields: ["name", "ref", "highway"] },
  { id: "rivers", label: "Rivers", layer: "lines", base: [{ key: "waterway", values: [] }],
    pick: { key: "waterway", label: "Waterway types", defaults: ["river"], suggest: ["river", "stream", "canal", "drain"] },
    fields: ["name", "waterway"] },
  { id: "water", label: "Lakes & water bodies", layer: "multipolygons", base: [{ key: "natural", values: ["water"] }],
    pick: { key: "water", label: "Kinds of water", defaults: [], suggest: ["lake", "reservoir", "pond", "river"] },
    fields: ["name", "water"] },
  { id: "glaciers", label: "Glaciers", layer: "multipolygons", base: [{ key: "natural", values: ["glacier"] }], pick: null, fields: ["name"] },
  { id: "parks", label: "National parks", layer: "multipolygons", base: [{ key: "boundary", values: [] }],
    pick: { key: "boundary", label: "Kinds of protected area", defaults: ["national_park"], suggest: ["national_park", "protected_area"] },
    fields: ["name", "boundary"] },
  { id: "coast", label: "Coastline", layer: "lines", base: [{ key: "natural", values: ["coastline"] }], pick: null, fields: [] },
];

export const MODES = [
  { id: "admin", label: "Admin boundaries" },
  ...PRESETS.map((p) => ({ id: p.id, label: p.label })),
  { id: "custom", label: "Something else" },
];

const SHAPES = [["multipolygons", "Areas"], ["lines", "Lines"], ["points", "Points"]];
const FEW = 12; // properties shown before "Show all"

/** Everything below the mode picker for the non-admin kinds. Reports the map preview up through onView. */
export default function ExtractPanel({ ds, mode, onView }) {
  const preset = PRESETS.find((p) => p.id === mode);
  const [shape, setShape] = useState("multipolygons");
  const [pickKey, setPickKey] = useState("");
  const [picked, setPicked] = useState([]);
  const [fields, setFields] = useState([]);
  const [showAll, setShowAll] = useState(false);
  const [search, setSearch] = useState("");
  const [scan, setScan] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [err, setErr] = useState("");

  const layer = preset?.layer ?? shape;
  const key = preset ? preset.pick?.key ?? "" : pickKey;

  useEffect(() => {
    setPicked(preset?.pick?.defaults ?? []);
    setFields(preset?.fields ?? ["name"]);
    setPickKey(""); setShowAll(false); setSearch(""); setErr("");
  }, [mode]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    let stale = false;
    setScanning(true); setScan(null);
    api("/technical/osm/fields", { method: "POST", body: { token: ds.token, layer } })
      .then((r) => { if (!stale) { setScan(r); setScanning(false); } })
      .catch((e) => { if (!stale) { setErr(errorText(e)); setScanning(false); } });
    return () => { stale = true; };
  }, [ds.token, layer]);

  const filters = useMemo(() => {
    const f = (preset?.base ?? []).map((b) => ({ ...b }));
    if (key && picked.length) f.push({ key, values: picked });
    return f;
  }, [preset, key, picked]);
  const filtersKey = JSON.stringify(filters);

  useEffect(() => {
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/osm/preview", { method: "POST", body: { token: ds.token, layer, filters, limit: 2000 } })
        .then((r) => { if (stale || r.stale) return; setRes({ ...r, key: `${mode}|${layer}|${filtersKey}` }); setErr(""); setBusy(false); })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    }, 400);
    return () => { stale = true; clearTimeout(t); };
  }, [ds.token, mode, layer, filtersKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { onView({ res, busy }); }, [res, busy]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => () => onView(null), []); // eslint-disable-line react-hooks/exhaustive-deps

  const info = useMemo(() => new Map((scan?.fields ?? []).map((f) => [f.key, f])), [scan]);
  const options = useMemo(() => {
    const known = new Map(info.get(key)?.values ?? []);
    const names = [...new Set([...picked, ...(preset?.pick?.suggest ?? []), ...known.keys()])];
    return names.map((v) => [v, known.get(v)]);
  }, [info, key, picked, preset]);

  const allFields = useMemo(
    () => [...(scan?.fields ?? []).filter((f) => f.count > 0), ...fields.filter((k) => !info.get(k)?.count).map((k) => ({ key: k, count: 0 }))],
    [scan, fields, info],
  );
  const shownFields = useMemo(() => {
    const s = search.trim().toLowerCase();
    const list = allFields.filter((f) => !s || f.key.toLowerCase().includes(s));
    return showAll ? list : list.slice(0, FEW);
  }, [allFields, search, showAll]);

  const toggle = (set, v) => set((l) => (l.includes(v) ? l.filter((x) => x !== v) : [...l, v]));

  const exportFile = async () => {
    setExporting(true); setErr("");
    try {
      const r = await fetch("/api/technical/osm/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: ds.token, layer, filters, fields, name: mode }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || "osm.topojson";
      download(await r.blob(), name);
    } catch (e) { setErr(e.message); } finally { setExporting(false); }
  };

  const pickTitle = preset ? preset.pick?.label : key;
  return <>
    {err && <ErrorBox message={err} />}

    {mode === "custom" && <>
      <div className="tp-field">
        <label htmlFor="ex-shape">Shape</label>
        <select id="ex-shape" value={shape} onChange={(e) => { setShape(e.target.value); setPickKey(""); setPicked([]); }}>
          {SHAPES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      <div className="tp-field">
        <label htmlFor="ex-key">Only the ones with a value for</label>
        <select id="ex-key" value={pickKey} disabled={!scan} onChange={(e) => { setPickKey(e.target.value); setPicked([]); }}>
          <option value="">{scanning ? "Reading the file…" : "Nothing: take everything"}</option>
          {(scan?.fields ?? []).filter((f) => f.values.length).map((f) => <option key={f.key} value={f.key}>{f.key} ({f.count.toLocaleString()})</option>)}
        </select>
      </div>
    </>}

    {!!key && <>
      <h3>{pickTitle}</h3>
      {scanning && !options.length ? <p className="muted">Reading the file…</p> : <div className="tp-tags">
        {options.map(([v, n]) => <label className="tp-tag" key={v} title={v}>
          <input type="checkbox" checked={picked.includes(v)} onChange={() => toggle(setPicked, v)} />
          <span className="tp-dot" style={{ background: "transparent" }} />
          <span>{v}</span><span className="muted">{n ? n.toLocaleString() : ""}</span>
        </label>)}
      </div>}
      {!picked.length && <p className="muted">Nothing ticked: everything of this kind is included.</p>}
    </>}

    <h3>Properties to keep</h3>
    {showAll && <input type="search" aria-label="Search properties" placeholder="Search properties" value={search} onChange={(e) => setSearch(e.target.value)} />}
    <div className="tp-tags">
      {shownFields.map((f) => <label className="tp-tag" key={f.key} title={f.key}>
        <input type="checkbox" checked={fields.includes(f.key)} onChange={() => toggle(setFields, f.key)} />
        <span className="tp-dot" style={{ background: "transparent" }} />
        <span>{f.key}</span><span className="muted">{f.count ? f.count.toLocaleString() : ""}</span>
      </label>)}
      {!shownFields.length && <p className="muted">{scanning ? "Reading the file…" : "No properties found."}</p>}
    </div>
    {allFields.length > FEW && <button type="button" className="link-btn" onClick={() => setShowAll((v) => !v)}>{showAll ? "Show fewer" : `Show all ${allFields.length}`}</button>}
    {scan?.partial && <p className="muted">Options come from the first {scan.sampled.toLocaleString()} features.</p>}

    <h3>Export</h3>
    <button type="button" className="btn ghost" disabled={exporting} onClick={exportFile}>{exporting ? "Building…" : "TopoJSON"}</button>
  </>;
}
