import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import ExtractPanel, { MODES } from "./ExtractPanel.jsx";
import { download } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import SplitCard from "./SplitCard.jsx";
import "./technical.css";

const COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
const colorOf = (level) => COLORS[level % COLORS.length];
const PREVIEW_PCT = 5; // same floor as the server
const memo = { ds: null, mode: "admin", country: "", levels: [], simplify: 0 };

export default function AdminExtractor() {
  const [ds, setDs] = useState(memo.ds);
  const [mode, setMode] = useState(memo.mode); // "admin" or one of the kinds in ExtractPanel
  const [view, setView] = useState(null); // { res, busy } of the preview of a non-admin kind
  const [country, setCountry] = useState(memo.country);
  const [levels, setLevels] = useState(memo.levels);
  const [simplify, setSimplify] = useState(memo.simplify);
  const [pct, setPct] = useState(Math.max(PREVIEW_PCT, memo.simplify)); // debounced preview simplification
  const [info, setInfo] = useState(null); // { levels: [{ level, count }] } of the selected country only
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const [ver, setVer] = useState(0);
  const cache = useRef(new Map()); // `${token}|${country}|${level}|${pct}` -> features (a level is fetched once)
  const last = useRef({ k: "", fc: null });
  const pick = useRef(null);
  memo.ds = ds; memo.mode = mode; memo.country = country; memo.levels = levels; memo.simplify = simplify;

  const accept = (d) => {
    cache.current.clear();
    setDs(d); setCountry(""); setLevels([]); setInfo(null); setErr(""); setView(null);
  };

  const upload = async (file) => {
    if (!file) return;
    setLoading(true); setErr("");
    try {
      const r = await fetch(`/api/technical/pbf/upload?filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      accept(await r.json());
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  // A file already on this machine: nothing is copied, so big files open much faster.
  const openPath = async (p = path) => {
    if (!p.trim()) return false;
    setLoading(true); setErr("");
    try { accept(await api("/technical/pbf/open", { method: "POST", body: { path: p } })); return true; }
    catch (e) { setErr(errorText(e)); return false; } finally { setLoading(false); }
  };

  // Step 1: build the selected country (slow once, instant when the server already has it).
  useEffect(() => {
    setInfo(null);
    if (!ds || !country) { setExtracting(false); return undefined; }
    let stale = false;
    setExtracting(true); setErr("");
    api("/technical/pbf/country", { method: "POST", body: { token: ds.token, country } })
      .then((r) => {
        if (stale) return;
        setInfo(r); setExtracting(false);
        setLevels((ls) => ls.filter((l) => r.levels.some((x) => x.level === l)));
      })
      .catch((e) => { if (!stale) { setErr(errorText(e)); setExtracting(false); } });
    return () => { stale = true; };
  }, [ds, country]);

  // Slider: the preview follows it after a short pause.
  useEffect(() => {
    const t = setTimeout(() => setPct(Math.max(PREVIEW_PCT, simplify)), 250);
    return () => clearTimeout(t);
  }, [simplify]);

  // Step 2: only fetch levels we don't have yet. Toggling a level that was already shown is purely local.
  // The server finishes a new level on its first request (cut to the coast, gaps filled with finer levels).
  useEffect(() => {
    if (!ds || !country || !info) return undefined;
    const suffix = `|${pct}`;
    for (const k of [...cache.current.keys()]) if (!k.endsWith(suffix)) cache.current.delete(k);
    const prefix = `${ds.token}|${country}|`;
    const missing = levels.filter((l) => !cache.current.has(`${prefix}${l}${suffix}`));
    if (!missing.length) { setBusy(false); return undefined; }
    let stale = false;
    setBusy(true);
    api("/technical/pbf/preview", { method: "POST", body: { token: ds.token, country, levels: missing, simplify: pct } })
      .then((r) => {
        for (const [l, feats] of Object.entries(r.levels)) cache.current.set(`${prefix}${l}${suffix}`, feats);
        setVer((v) => v + 1);
        if (!stale) { setBusy(false); setErr(""); }
      })
      .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    return () => { stale = true; };
  }, [ds, country, info, levels, pct]);

  // What the map shows: the cached levels that are ticked. While a new level loads, the rest stays on screen.
  const fc = useMemo(() => {
    const k = ds && country ? `${ds.token}|${country}` : "";
    if (!k || !levels.length) { last.current = { k, fc: null }; return null; }
    const features = levels.flatMap((l) => cache.current.get(`${k}|${l}|${pct}`) ?? []);
    if (features.length) last.current = { k, fc: { type: "FeatureCollection", features } };
    else if (last.current.k !== k) last.current = { k, fc: null };
    return last.current.fc;
  }, [ds, country, levels, pct, ver]);

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

  const toggleLevel = (level) => setLevels((ls) => (ls.includes(level) ? ls.filter((x) => x !== level) : [...ls, level].sort((a, b) => a - b)));

  const admin = mode === "admin";
  const other = view?.res;

  return <div className="tp">
    <div className="tp-side">
      <div className={`tp-drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
        <h3>OSM PBF file</h3>
        <div className="tp-row">
          <input id="pbf-path" value={path} placeholder="C:\maps\argentina-latest.osm.pbf" onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") openPath(); }} />
          <button type="button" className="btn ghost"
            onClick={async () => { if (path.trim() && await openPath()) return; pick.current?.click(); }}>Open</button>
          <input ref={pick} type="file" accept=".osm.pbf" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; upload(f); }} />
        </div>
      </div>

      {ds && <SplitCard ds={ds} onOpen={(p) => { setPath(p); openPath(p); }} />}

      {ds && <>
        <h3>What to extract</h3>
        <div className="tabs">
          {MODES.map((m) => <button key={m.id} type="button" className={m.id === mode ? "on" : ""} aria-pressed={m.id === mode}
            onClick={() => { setMode(m.id); setErr(""); }}>{m.label}</button>)}
        </div>
      </>}

      {ds && !admin && <ExtractPanel ds={ds} mode={mode} onView={setView} />}

      {ds && admin && <>
        <h3>Country</h3>
        <input type="search" aria-label="Search countries" placeholder="Search countries" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="tp-tags">
          {shown.map((c) => <label className="tp-tag" key={c.id} title={c.name}>
            <input type="radio" name="pbf-country" checked={country === c.id} onChange={() => setCountry(c.id)} />
            <span className="tp-dot" style={{ background: c.code ? "var(--line)" : "transparent" }} />
            <span>{c.name}</span><span className="muted">{c.code || c.id}</span>
          </label>)}
          {!shown.length && <p className="muted">No match.</p>}
        </div>

        {country && extracting && <p className="muted">Building the boundaries of this country. The first time takes a while (it reads the file once); after that, everything here is instant.</p>}

        {country && info && <>
          <h3>Admin levels <span className="muted">· in this country</span></h3>
          <div className="tp-tags">
            {info.levels.map((l) => <label className="tp-tag" key={l.level} title={`admin_level=${l.level}`}>
              <input type="checkbox" checked={levels.includes(l.level)} onChange={() => toggleLevel(l.level)} />
              <span className="tp-dot" style={{ background: colorOf(l.level) }} />
              <span>Level {l.level}</span><span className="muted">{l.count.toLocaleString()}</span>
            </label>)}
          </div>

          <h3>Simplification</h3>
          <div className="tp-field">
            <label htmlFor="pbf-simplify">Export <span className="muted">{simplify ? `${simplify}%` : "Off"}</span></label>
            <input id="pbf-simplify" type="range" min="0" max="100" step="1" value={simplify} onChange={(e) => setSimplify(Number(e.target.value))} />
          </div>

          <h3>Export</h3>
          <button type="button" className="btn ghost" disabled={!levels.length || exporting} onClick={exportTopo}>
            {exporting ? "Building…" : "TopoJSON"}
          </button>
        </>}
      </>}
    </div>

    <div className="tp-main">
      {err && <ErrorBox message={err} />}
      {admin && (extracting || busy) && <p className="muted">{extracting ? "Building this country…" : "Loading… a new level is cut to the coast and its gaps are filled the first time."}</p>}
      {admin && ds && country && <PolyMap features={fc} fitKey={fc ? `${ds.token}|${country}` : null} />}
      {!admin && ds && <>
        <PolyMap features={other?.geojson?.features.length ? other.geojson : null} fitKey={other?.key} />
        <div className="tp-stats muted">
          {view?.busy ? "Searching the file…" : other
            ? (other.count ? `${other.count.toLocaleString()} shown${other.truncated ? " (preview limit; the export has all of them)" : ""}` : "Nothing matches.")
            : ""}
        </div>
      </>}
    </div>
  </div>;
}
