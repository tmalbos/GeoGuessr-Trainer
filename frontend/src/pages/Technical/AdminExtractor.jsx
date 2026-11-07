import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import StatusIcon from "../../components/StatusIcon.jsx";
import { api, errorText } from "../../lib/api.js";
import ExportDialog from "./ExportDialog.jsx";
import ExtractPanel, { MODES } from "./ExtractPanel.jsx";
import { download } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import SplitCard from "./SplitCard.jsx";
import "./technical.css";

const COLORS = ["#4e79a7", "#f28e2b", "#e15759", "#76b7b2", "#59a14f", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac"];
const UNNAMED_COLOR = "#e5484d"; // keep equal to UNNAMED_COLOR in pbf_admin.py
const colorOf = (level) => COLORS[level % COLORS.length];
const PREVIEW_PCT = 5; // same floor as the server
const memo = { ds: null, mode: "admin", country: "", levels: [] };

export default function AdminExtractor() {
  const [ds, setDs] = useState(memo.ds);
  const [mode, setMode] = useState(memo.mode); // "admin" or one of the kinds in ExtractPanel
  const [view, setView] = useState(null); // { res, busy } of the preview of a non-admin kind
  const [country, setCountry] = useState(memo.country);
  const [levels, setLevels] = useState(memo.levels);
  const pct = PREVIEW_PCT; // the preview is always lightly simplified; the export dialog sets the real amount
  const [info, setInfo] = useState(null); // { levels: [{ level, count }] } of the selected country only
  const [fills, setFills] = useState({}); // level -> level whose polygons fill its gaps (absent = "Unnamed N")
  const [picked, setPicked] = useState(null); // properties of the clicked polygon
  const [marks, setMarks] = useState([]); // polygons picked with click / Shift+click, to merge
  const [nameDraft, setNameDraft] = useState("");
  const [bump, setBump] = useState(0); // forces a refetch of levels removed from the cache
  const [path, setPath] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const [ver, setVer] = useState(0);
  const cache = useRef(new Map()); // `${token}|${country}|${level}|${pct}` -> features (a level is fetched once)
  const last = useRef({ k: "", fc: null });
  const pick = useRef(null);
  memo.ds = ds; memo.mode = mode; memo.country = country; memo.levels = levels;

  const accept = (d) => {
    cache.current.clear();
    setDs(d); setCountry(""); setLevels([]); setInfo(null); setErr(""); setView(null); setPicked(null); setFills({}); setMarks([]);
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
    setInfo(null); setPicked(null); setMarks([]);
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

  // Delete/Backspace drops the picked polygon from what the map shows (the export is built from the cache).
  const removePicked = () => {
    if (!picked) return;
    const key = `${ds.token}|${country}|${picked.admin_level}|${pct}`;
    const feats = cache.current.get(key);
    if (feats) {
      const drop = picked.gap != null
        ? (p) => p.admin_level === picked.admin_level && p.gap === picked.gap
        : (p) => p.tag === picked.tag;
      cache.current.set(key, feats.filter((f) => !drop(f.properties)));
    }
    setPicked(null);
    setVer((v) => v + 1);
  };

  useEffect(() => {
    const onKey = (e) => {
      if (e.key !== "Delete" && e.key !== "Backspace" && e.keyCode !== 46 && e.keyCode !== 8) return;
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      if (mode !== "admin" || !picked) return;
      e.preventDefault();
      removePicked();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [picked, ds, country, pct, mode]); // eslint-disable-line react-hooks/exhaustive-deps

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
      setPicked(null); setMarks([]);
      setVer((v) => v + 1);
      setBump((b) => b + 1);
    } catch (e) { setErr(errorText(e)); }
  };

  const onPick = (p, shift) => {
    const m = p && p.idx != null ? p : null;
    if (!shift) { setPicked(p); setMarks(m ? [m] : []); return; }
    if (!m) return;
    setMarks((ms) => {
      const same = ms.filter((x) => x.admin_level === m.admin_level);
      if (same.some((x) => x.idx === m.idx)) return same.filter((x) => x.idx !== m.idx);
      return [...same, m];
    });
  };

  const mergeMarked = async () => {
    const first = marks[0];
    setErr("");
    try {
      await api("/technical/pbf/merge", { method: "POST", body: { token: ds.token, country, level: first.admin_level, idxs: marks.map((m) => m.idx) } });
      cache.current.delete(`${ds.token}|${country}|${first.admin_level}|${pct}`);
      setMarks([]); setPicked(null);
      setVer((v) => v + 1);
      setBump((n) => n + 1);
    } catch (e) { setErr(errorText(e)); }
  };

  // Called by the export dialog (name, adm, simplify, parents). It throws so the dialog can show the error.
  const exportTopo = async (settings) => {
    const r = await fetch("/api/technical/pbf/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: ds.token, country, levels, ...settings }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    const name = (r.headers.get("Content-Disposition") || "").match(/filename="?([^";]+)/)?.[1] || "ADM.topojson";
    download(await r.blob(), name);
    setExportOpen(false);
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

          {!!levels.length && <div className="tp-field">
            <label>Merge <span className="muted">{marks.length} selected</span></label>
            <p className="muted">Click a polygon, then Shift+click more of the same level.</p>
            <button type="button" className="btn ghost" disabled={marks.length < 2 || !levels.includes(marks[0].admin_level)} onClick={mergeMarked}>Merge selected</button>
          </div>}

          <h3>Export</h3>
          <button type="button" className="btn ghost" disabled={!levels.length} onClick={() => setExportOpen(true)}>TopoJSON</button>
        </>}
      </>}
    </div>

    <div className="tp-main">
      {exportOpen && info && <ExportDialog ds={ds} levels={info.levels} selected={levels} onClose={() => setExportOpen(false)} onExport={exportTopo} />}
      {err && <ErrorBox message={err} />}
      {admin && (extracting || busy) && <p className="muted" role="status"><StatusIcon kind="wait" />{extracting ? "Building this country…" : "Loading… a new level is cut to the coast and its gaps are filled the first time."}</p>}
      {admin && ds && country && <PolyMap features={fc} fitKey={fc ? `${ds.token}|${country}` : null} onPick={onPick} selected={sel?.tag ?? null} marked={marks} />}
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
