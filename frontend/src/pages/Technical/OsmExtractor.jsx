import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { download } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import "./technical.css";

// Shortcuts only: each one just fills in the layer, filters and fields below, which can be changed freely.
const PRESETS = [
  { label: "Roads", layer: "lines", filters: [{ key: "highway", values: ["motorway", "trunk", "primary", "secondary"] }], fields: ["name", "highway", "ref"] },
  { label: "Numbered routes", layer: "multilinestrings", filters: [{ key: "route", values: ["road"] }, { key: "ref", values: [] }], fields: ["name", "ref", "type", "route"] },
  { label: "Coastline", layer: "lines", filters: [{ key: "natural", values: ["coastline"] }], fields: [] },
  { label: "Glaciers", layer: "multipolygons", filters: [{ key: "natural", values: ["glacier"] }], fields: ["name"] },
  { label: "Lakes & ponds", layer: "multipolygons", filters: [{ key: "natural", values: ["water"] }], fields: ["name", "water"] },
  { label: "Rivers", layer: "lines", filters: [{ key: "waterway", values: ["river"] }], fields: ["name", "waterway"] },
  { label: "National parks", layer: "multipolygons", filters: [{ key: "boundary", values: ["national_park", "protected_area"] }], fields: ["name", "boundary"] },
  { label: "Admin boundaries", layer: "multipolygons", filters: [{ key: "boundary", values: ["administrative"] }], fields: ["name", "admin_level"] },
];

const STRATEGIES = [
  ["smart", "Smart: complete relations (slowest, best)"],
  ["complete_ways", "Complete ways (faster)"],
  ["simple", "Simple: cut at the border (fastest)"],
];

const memo = { ds: null };
const size = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);

export default function OsmExtractor() {
  const [ds, setDs] = useState(memo.ds); // { token, file, layers }
  const [path, setPath] = useState("");
  const [loading, setLoading] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const [layer, setLayer] = useState("multipolygons");
  const [filters, setFilters] = useState([]); // [{ key, values }]
  const [fields, setFields] = useState(["name"]);
  const [scan, setScan] = useState(null); // { fields: [{ key, count, values }], sampled, partial }
  const [scanning, setScanning] = useState(false);
  const [search, setSearch] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [co, setCo] = useState({ state: "idle", list: [] });
  const [sel, setSel] = useState([]);
  const [folder, setFolder] = useState("");
  const [strategy, setStrategy] = useState("smart");
  const [splitting, setSplitting] = useState(false);
  const [splitRes, setSplitRes] = useState(null);
  const input = useRef(null);
  memo.ds = ds;
  const filtersKey = JSON.stringify(filters);

  const accept = (d) => {
    setDs(d); setLayer("multipolygons"); setFilters([]); setFields(["name"]); setScan(null);
    setRes(null); setSplitRes(null); setSel([]); setErr("");
  };

  const open = async (p) => {
    if (!p.trim()) return;
    setLoading(true); setErr("");
    try { accept(await api("/technical/osm/open", { method: "POST", body: { path: p } })); }
    catch (e) { setErr(errorText(e)); } finally { setLoading(false); }
  };

  const upload = async (file) => {
    if (!file) return;
    setLoading(true); setErr("");
    try {
      const r = await fetch(`/api/technical/osm/upload?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      accept(await r.json());
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  // Is there more than one country in the file? (checked once per file, in the background)
  useEffect(() => {
    if (!ds) { setCo({ state: "idle", list: [] }); return undefined; }
    let stale = false;
    setCo({ state: "checking", list: [] });
    api("/technical/osm/countries", { method: "POST", body: { token: ds.token } })
      .then((r) => { if (!stale) { setCo({ state: "done", list: r.countries }); setSel(r.countries.map((c) => c.id)); } })
      .catch(() => { if (!stale) setCo({ state: "error", list: [] }); });
    return () => { stale = true; };
  }, [ds]);

  // The fields this layer really has (read from a sample of the file).
  useEffect(() => {
    if (!ds) return undefined;
    let stale = false;
    setScanning(true); setScan(null);
    api("/technical/osm/fields", { method: "POST", body: { token: ds.token, layer } })
      .then((r) => { if (!stale) { setScan(r); setScanning(false); } })
      .catch((e) => { if (!stale) { setErr(errorText(e)); setScanning(false); } });
    return () => { stale = true; };
  }, [ds, layer]);

  // Preview follows the layer and the filters.
  useEffect(() => {
    if (!ds) { setRes(null); return undefined; }
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/osm/preview", { method: "POST", body: { token: ds.token, layer, filters, limit: 2000 } })
        .then((r) => { if (stale || r.stale) return; setRes({ ...r, key: `${layer}|${filtersKey}` }); setErr(""); setBusy(false); })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    }, 400);
    return () => { stale = true; clearTimeout(t); };
  }, [ds, layer, filtersKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const info = useMemo(() => new Map((scan?.fields ?? []).map((f) => [f.key, f])), [scan]);
  const allFields = useMemo(
    () => [...(scan?.fields ?? []), ...fields.filter((k) => !info.has(k)).map((k) => ({ key: k, count: 0 }))],
    [scan, fields, info],
  );
  const shown = useMemo(() => {
    const s = search.trim().toLowerCase();
    return allFields.filter((f) => !s || f.key.toLowerCase().includes(s));
  }, [allFields, search]);

  const applyPreset = (p) => {
    setLayer(p.layer);
    setFilters(p.filters.map((f) => ({ key: f.key, values: [...f.values] })));
    setFields([...p.fields]);
  };
  const setFilter = (i, patch) => setFilters((fs) => fs.map((f, j) => (j === i ? { ...f, ...patch } : f)));
  const toggleValue = (i, v) => setFilter(i, { values: filters[i].values.includes(v) ? filters[i].values.filter((x) => x !== v) : [...filters[i].values, v] });
  const toggleField = (k) => setFields((fs) => (fs.includes(k) ? fs.filter((x) => x !== k) : [...fs, k]));

  const exportFile = async () => {
    setExporting(true); setErr("");
    try {
      const r = await fetch("/api/technical/osm/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: ds.token, layer, filters, fields }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || "osm.geojson";
      download(await r.blob(), name);
    } catch (e) { setErr(e.message); } finally { setExporting(false); }
  };

  const split = async () => {
    setSplitting(true); setErr(""); setSplitRes(null);
    try { setSplitRes(await api("/technical/osm/split", { method: "POST", body: { token: ds.token, countries: sel, folder, strategy } })); }
    catch (e) { setErr(errorText(e)); } finally { setSplitting(false); }
  };

  const toggleCountry = (id) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));

  return <div className="tp">
    <div className="tp-side">
      <div className={`tp-drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
        <b>OSM PBF file</b>
        <span className="muted">{loading ? "Reading the file…" : ds ? `${ds.file.name} · ${size(ds.file.size)}` : "Drop the .osm.pbf here"}</span>
        <button type="button" className="btn ghost" disabled={loading} onClick={() => input.current.click()}>{ds ? "Load another" : "Choose file"}</button>
        <input ref={input} type="file" accept=".pbf,.osm.pbf" hidden onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
        <label htmlFor="osm-path" className="muted">…or a path on this machine (no copy, faster)</label>
        <div className="tp-row">
          <input id="osm-path" value={path} placeholder="C:\maps\argentina-latest.osm.pbf" onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") open(path); }} />
          <button type="button" className="btn ghost" disabled={loading || !path.trim()} onClick={() => open(path)}>Open</button>
        </div>
      </div>

      {ds && co.state === "checking" && <p className="muted">Checking whether the file holds several countries…</p>}
      {ds && co.state === "done" && co.list.length > 1 && <div className="tp-drop">
        <b>{co.list.length} countries in this file</b>
        <span className="muted">Split it into one .osm.pbf per country. Smaller files scan and extract much faster.</span>
        <div className="tp-row">
          <button type="button" className="btn ghost" onClick={() => setSel(co.list.map((c) => c.id))}>All</button>
          <button type="button" className="btn ghost" onClick={() => setSel([])}>None</button>
          <span className="muted">{sel.length} selected</span>
        </div>
        <div className="tp-tags" style={{ width: "100%" }}>
          {co.list.map((c) => <label className="tp-tag" key={c.id} title={c.name}>
            <input type="checkbox" checked={sel.includes(c.id)} onChange={() => toggleCountry(c.id)} />
            <span className="tp-dot" style={{ background: "transparent" }} />
            <span>{c.name}</span><span className="muted">{c.code}</span>
          </label>)}
        </div>
        <label htmlFor="osm-folder" className="muted">Save in {ds.file.name && "(empty = a folder next to the file)"}</label>
        <input id="osm-folder" value={folder} placeholder="C:\maps\countries" onChange={(e) => setFolder(e.target.value)} />
        <select aria-label="Split method" value={strategy} onChange={(e) => setStrategy(e.target.value)}>
          {STRATEGIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <button type="button" className="btn" disabled={splitting || !sel.length} onClick={split}>{splitting ? "Splitting…" : `Split into ${sel.length} file${sel.length === 1 ? "" : "s"}`}</button>
        {splitting && <span className="muted">This can take several minutes for a big file. Needs osmium-tool.</span>}
        {splitRes && <div style={{ width: "100%" }}>
          <span className="muted">Saved in {splitRes.folder}</span>
          {splitRes.files.map((f) => <div className="tp-row" key={f.path} style={{ justifyContent: "space-between", marginTop: 6 }}>
            <span title={f.path}>{f.country} <span className="muted">· {size(f.size)}</span></span>
            <button type="button" className="btn ghost" disabled={loading} onClick={() => { setPath(f.path); open(f.path); }}>Open</button>
          </div>)}
        </div>}
      </div>}

      {ds && <>
        <h3>Quick start</h3>
        <div className="tp-row">
          {PRESETS.map((p) => <button key={p.label} type="button" className="btn ghost" onClick={() => applyPreset(p)}>{p.label}</button>)}
        </div>

        <h3>What to extract</h3>
        <div className="tp-field">
          <label htmlFor="osm-layer">Kind of feature</label>
          <select id="osm-layer" value={layer} onChange={(e) => { setLayer(e.target.value); setFilters([]); }}>
            {ds.layers.map((l) => <option key={l.id} value={l.id}>{l.label}</option>)}
          </select>
        </div>

        <h3>Filters {filters.length > 0 && <span className="muted">· all must match</span>}</h3>
        {filters.map((f, i) => {
          const common = info.get(f.key)?.values ?? [];
          const options = [...new Set([...f.values, ...common.map((v) => v[0])])];
          const count = new Map(common);
          return <div className="tp-field" key={f.key}>
            <label>{f.key}<button type="button" className="link-btn" onClick={() => setFilters((fs) => fs.filter((_, j) => j !== i))}>Remove</button></label>
            {!f.values.length && <span className="muted">Any value (the key only has to exist)</span>}
            {!!options.length && <div className="tp-tags" style={{ maxHeight: 180 }}>
              {options.map((v) => <label className="tp-tag" key={v} title={v}>
                <input type="checkbox" checked={f.values.includes(v)} onChange={() => toggleValue(i, v)} />
                <span className="tp-dot" style={{ background: "transparent" }} />
                <span>{v}</span><span className="muted">{count.get(v)?.toLocaleString() ?? ""}</span>
              </label>)}
            </div>}
            <input aria-label={`Another value for ${f.key}`} placeholder="Another value, then Enter"
              onKeyDown={(e) => {
                if (e.key !== "Enter") return;
                const v = e.currentTarget.value.trim();
                if (v && !f.values.includes(v)) setFilter(i, { values: [...f.values, v] });
                e.currentTarget.value = "";
              }} />
          </div>;
        })}
        <select aria-label="Add a filter" value="" disabled={!scan} onChange={(e) => { if (e.target.value) setFilters((fs) => [...fs, { key: e.target.value, values: [] }]); }}>
          <option value="">{scanning ? "Reading the fields…" : "Add a filter on…"}</option>
          {(scan?.fields ?? []).filter((f) => !filters.some((x) => x.key === f.key)).map((f) => <option key={f.key} value={f.key}>{f.key} ({f.count.toLocaleString()})</option>)}
        </select>

        <h3>Fields to export {fields.length > 0 && <span className="muted">· {fields.length} selected</span>}</h3>
        <input type="search" aria-label="Search fields" placeholder="Search fields" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="tp-row">
          <button type="button" className="btn ghost" onClick={() => setFields((s) => [...new Set([...s, ...shown.slice(0, 30).map((f) => f.key)])])}>Add shown (max 30)</button>
          <button type="button" className="btn ghost" disabled={!fields.length} onClick={() => setFields([])}>Clear</button>
        </div>
        <div className="tp-tags">
          {shown.map((f) => <label className="tp-tag" key={f.key} title={f.key}>
            <input type="checkbox" checked={fields.includes(f.key)} onChange={() => toggleField(f.key)} />
            <span className="tp-dot" style={{ background: "transparent" }} />
            <span>{f.key}</span><span className="muted">{f.count ? f.count.toLocaleString() : ""}</span>
          </label>)}
          {!shown.length && <p className="muted">{scanning ? "Reading the fields…" : "No field matches."}</p>}
        </div>
        {scan?.partial && <p className="muted">Fields and counts come from the first {scan.sampled.toLocaleString()} features of this layer.</p>}

        <h3>Export</h3>
        <div className="tp-row">
          <button type="button" className="btn ghost" disabled={exporting} onClick={exportFile}>{exporting ? "Building…" : "GeoJSON"}</button>
        </div>
      </>}
    </div>

    <div className="tp-main">
      {err && <ErrorBox message={err} />}
      {!ds && <p className="muted">Open a .osm.pbf, pick what you want (or start from a shortcut) and it shows up here.</p>}
      {ds && <PolyMap features={res?.geojson?.features.length ? res.geojson : null} fitKey={res?.key} />}
      {ds && busy && <div className="tp-stats muted">Searching the file…</div>}
      {ds && !busy && res && <div className="tp-stats muted">
        {res.count ? `${res.count.toLocaleString()} features shown${res.truncated ? " (preview limit; the export has all of them)" : ""}` : "Nothing matches these filters."}
      </div>}
    </div>
  </div>;
}
