import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import StatusIcon from "../../components/StatusIcon.jsx";
import { api, errorText } from "../../lib/api.js";
import ExtractPanel, { MODES } from "./ExtractPanel.jsx";
import { download } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import SplitCard from "./SplitCard.jsx";
import "./technical.css";

const COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
const UNNAMED_COLOR = "#e5484d"; // keep equal to UNNAMED_COLOR in pbf_admin.py
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
  const [fills, setFills] = useState({}); // level -> level whose polygons fill its gaps (absent = "Unnamed N")
  const [picked, setPicked] = useState(null); // properties of the clicked polygon
  const [nameDraft, setNameDraft] = useState("");
  const [bump, setBump] = useState(0); // forces a refetch of levels removed from the cache
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
    setDs(d); setCountry(""); setLevels([]); setInfo(null); setErr(""); setView(null); setPicked(null); setFills({});
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
    setInfo(null); setPicked(null);
    if (!ds || !country) { setExtracting(false); return undefined; }
    let stale = false;
    setExtracting(true); setErr("");
    api("/technical/pbf/country", { method: "POST", body: { token: ds.token, country } })
      .then((r) => {
        if (stale) return;
        setInfo(r); setFills(r.fills ?? {}); setExtracting(false);
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
  // The server finishes a new level on its first request (cut to the coast, gaps filled).
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
  }, [ds, country, info, levels, pct, bump]);

  // What the map shows: the cached levels that are ticked. While a new level loads, the rest stays on screen.
  const fc = useMemo(() => {
    const k = ds && country ? `${ds.token}|${country}` : "";
    if (!k || !levels.length) { last.current = { k, fc: null }; return null; }
    const features = levels.flatMap((l) => cache.current.get(`${k}|${l}|${pct}`) ?? []);
    if (features.length) last.current = { k, fc: { type: "FeatureCollection", features } };
    else if (last.current.k !== k) last.current = { k, fc: null };
    return last.current.fc;
  }, [ds, country, levels, pct, ver]);

  // The clicked polygon, only while it is a filled-in gap of a level that is still shown.
  const sel = picked && picked.gap != null && levels.includes(picked.admin_level) ? picked : null;
  useEffect(() => { setNameDraft(sel && !sel.unnamed ? sel.name : ""); }, [sel?.tag]); // eslint-disable-line react-hooks/exhaustive-deps

  const renameGap = async () => {
    if (!sel) return;
    const { admin_level: level, gap } = sel;
    const name = nameDraft.trim();
    setErr("");
    try {
      await api("/technical/pbf/rename", { method: "POST", body: { token: ds.token, country, level, gap, name } });
      const nm = name || `Unnamed ${gap}`;
      const patch = (p) => (p.gap === gap ? { ...p, name: nm, unnamed: !name, tag: `ADM${level} · ${nm}`, color: name ? colorOf(level) : UNNAMED_COLOR } : p);
      const key = `${ds.token}|${country}|${level}|${pct}`;
      const feats = cache.current.get(key);
      if (feats) cache.current.set(key, feats.map((f) => ({ ...f, properties: patch(f.properties) })));
      setPicked(patch(sel));
      setVer((v) => v + 1);
    } catch (e) { setErr(errorText(e)); }
  };

  const setFill = async (level, value) => {
    const source = value === "" ? null : Number(value);
    setErr("");
    try {
      await api("/technical/pbf/fill", { method: "POST", body: { token: ds.token, country, level, source } });
      setFills((f) => {
        const next = { ...f };
        if (source == null) delete next[level]; else next[level] = source;
        return next;
      });
      cache.current.delete(`${ds.token}|${country}|${level}|${pct}`);
      setPicked(null);
      setVer((v) => v + 1);
      setBump((b) => b + 1);
    } catch (e) { setErr(errorText(e)); }
  };

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
    } catch (e) { setErr(e.message); } finally { setExporting(false); }
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
            onKeyDown={(e) => { if (e.key === "Enter" && !loading) openPath(); }} />
          <button type="button" className="btn ghost" disabled={loading}
            onClick={async () => { if (path.trim() && await openPath()) return; pick.current?.click(); }}>{loading ? "Opening…" : "Open"}</button>
          <input ref={pick} type="file" accept=".osm.pbf" hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; upload(f); }} />
        </div>
        {loading && <span className="muted" role="status"><StatusIcon kind="wait" />Opening the file. A big file can take a few minutes the first time.</span>}
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

        {country && extracting && <p className="muted" role="status"><StatusIcon kind="wait" />Building the boundaries of this country. The first time takes a while (it reads the file once); after that, everything here is instant.</p>}

        {country && info && <>
          <h3>Admin levels <span className="muted">· in this country</span></h3>
          <div className="tp-tags">
            {info.levels.map((l) => <label className="tp-tag" key={l.level} title={`admin_level=${l.level}`}>
              <input type="checkbox" checked={levels.includes(l.level)} onChange={() => toggleLevel(l.level)} />
              <span className="tp-dot" style={{ background: colorOf(l.level) }} />
              <span>Level {l.level}</span><span className="muted">{l.count.toLocaleString()}</span>
            </label>)}
          </div>

          {!!levels.length && <>
            <h3>Gaps</h3>
            <p className="muted">Land a level doesn't cover appears in red as "Unnamed N". Click one on the map to rename it, or fill the gaps with another level.</p>
            {levels.map((l) => <div className="tp-field" key={l}>
              <label htmlFor={`fill-${l}`}>Level {l} gaps filled with</label>
              <select id={`fill-${l}`} value={fills[l] ?? ""} onChange={(e) => setFill(l, e.target.value)}>
                <option value="">Unnamed polygons</option>
                {info.levels.filter((x) => x.level !== l).map((x) => <option key={x.level} value={x.level}>Level {x.level} polygons</option>)}
              </select>
            </div>)}
          </>}

          {sel && <div className="tp-field">
            <label htmlFor="gap-name">Selected: Level {sel.admin_level} · {sel.unnamed ? sel.name : `${sel.name} (renamed)`}</label>
            <div className="tp-row">
              <input id="gap-name" value={nameDraft} placeholder={`Unnamed ${sel.gap}`} onChange={(e) => setNameDraft(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") renameGap(); }} />
              <button type="button" className="btn ghost" disabled={nameDraft.trim() === (sel.unnamed ? "" : sel.name)} onClick={renameGap}>Rename</button>
            </div>
          </div>}

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
      {admin && (extracting || busy) && <p className="muted" role="status"><StatusIcon kind="wait" />{extracting ? "Building this country…" : "Loading… a new level is cut to the coast and its gaps are filled the first time."}</p>}
      {admin && ds && country && <PolyMap features={fc} fitKey={fc ? `${ds.token}|${country}` : null} onPick={setPicked} selected={sel?.tag ?? null} />}
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
