import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { download } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import "./technical.css";

const COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
const colorOf = (level) => COLORS[level % COLORS.length];
const memo = { ds: null, country: "", levels: [], simplify: 0 };

export default function AdminExtractor() {
  const [ds, setDs] = useState(memo.ds);
  const [country, setCountry] = useState(memo.country);
  const [levels, setLevels] = useState(memo.levels);
  const [simplify, setSimplify] = useState(memo.simplify);
  const [search, setSearch] = useState("");
  const [res, setRes] = useState(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);
  memo.ds = ds; memo.country = country; memo.levels = levels; memo.simplify = simplify;

  const upload = async (file) => {
    if (!file) return;
    setLoading(true); setErr(""); setRes(null);
    try {
      const r = await fetch(`/api/technical/pbf/upload?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      setDs(await r.json()); setCountry(""); setLevels([]); setRes(null);
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  useEffect(() => {
    if (!ds || !country || !levels.length) { setRes(null); setBusy(false); return undefined; }
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/pbf/preview", { method: "POST", body: { token: ds.token, country, levels, simplify } })
        .then((r) => { if (!stale) { setRes(r); setErr(""); setBusy(false); } })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setRes(null); setBusy(false); } });
    }, 400);
    return () => { stale = true; clearTimeout(t); };
  }, [ds, country, levels, simplify]);

  const exportTopo = async () => {
    setExporting(true); setErr("");
    try {
      const r = await fetch("/api/technical/pbf/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ token: ds.token, country, levels, simplify }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const name = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || "admin.topojson";
      download(await r.blob(), name);
    } catch (e) { setErr(errorText(e)); } finally { setExporting(false); }
  };

  const shown = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (ds?.countries ?? []).filter((c) => !s || c.name.toLowerCase().includes(s) || c.code.toLowerCase().includes(s));
  }, [ds, search]);

  const counts = useMemo(() => {
    const out = Object.fromEntries((ds?.levels ?? []).map((l) => [l.level, l.count]));
    for (const l of res?.counts ?? []) out[l.level] = l.count;
    return out;
  }, [ds, res]);

  const toggleLevel = (level) => setLevels((ls) => (ls.includes(level) ? ls.filter((x) => x !== level) : [...ls, level].sort((a, b) => a - b)));

  return <div className="tp">
    <div className="tp-side">
      <div className={`tp-drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
        <b>OSM PBF file</b>
        <span className="muted">{loading ? "Reading…" : ds ? `${ds.file.name} · ${ds.countries.length} countries` : "Drop the .osm.pbf here"}</span>
        <button type="button" className="btn ghost" disabled={loading} onClick={() => input.current.click()}>{ds ? "Load another" : "Choose file"}</button>
        <input ref={input} type="file" accept=".pbf,.osm.pbf" hidden onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
      </div>

      {ds && <>
        <h3>Country</h3>
        <input type="search" aria-label="Search countries" placeholder="Search countries" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="tp-tags">
          {shown.map((c) => <label className="tp-tag" key={c.id} title={c.name}>
            <input type="radio" name="pbf-country" checked={country === c.id} onChange={() => { setCountry(c.id); setRes(null); }} />
            <span className="tp-dot" style={{ background: c.code ? "var(--line)" : "transparent" }} />
            <span>{c.name}</span><span className="muted">{c.code || c.id}</span>
          </label>)}
          {!shown.length && <p className="muted">No match.</p>}
        </div>

        {country && <>
          <h3>Admin levels</h3>
          <div className="tp-tags">
            {ds.levels.map((l) => <label className="tp-tag" key={l.level} title={`admin_level=${l.level}`}>
              <input type="checkbox" checked={levels.includes(l.level)} onChange={() => toggleLevel(l.level)} />
              <span className="tp-dot" style={{ background: colorOf(l.level) }} />
              <span>Level {l.level}</span><span className="muted">{counts[l.level] ?? l.count}</span>
            </label>)}
          </div>

          <h3>Simplification</h3>
          <div className="tp-field">
            <label htmlFor="pbf-simplify">Export <span className="muted">{simplify ? `${simplify}%` : "Off"}</span></label>
            <input id="pbf-simplify" type="range" min="0" max="100" step="1" value={simplify} onChange={(e) => setSimplify(Number(e.target.value))} />
          </div>

          <h3>Export</h3>
          <button type="button" className="btn ghost" disabled={!res || exporting || !levels.length} onClick={exportTopo}>
            {exporting ? "Building…" : "TopoJSON"}
          </button>
        </>}
      </>}
    </div>

    <div className="tp-main">
      {err && <ErrorBox message={err} />}
      {busy && <p className="muted">Computing…</p>}
      {res && <PolyMap features={res.geojson} fitKey={`${country}|${levels.join(",")}`} />}
    </div>
  </div>;
}
