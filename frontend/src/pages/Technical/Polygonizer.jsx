import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { download, svgToPng, toGeoJSON, toSVG, toTopoJSON } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import "./technical.css";

const DEFAULTS = { method: "blob", link_km: 100, pad_km: 10, ratio: 0.3, clip: false };
const memo = {}; // survives leaving the page: the uploaded file lives on the server, this remembers which one

const Slider = ({ label, value, min, max, step, unit = "", onChange }) => <div className="tp-field">
  <label>{label}<span className="muted">{value}{unit}</span></label>
  <input type="range" min={min} max={max} step={step} value={value} aria-label={label} onChange={(e) => onChange(Number(e.target.value))} />
</div>;

export default function Polygonizer() {
  const [ds, setDs] = useState(memo.ds ?? null); // { token, name, total, tags, points }
  const [sel, setSel] = useState(memo.sel ?? []);
  const [params, setParams] = useState(memo.params ?? DEFAULTS);
  const [showPts, setShowPts] = useState(memo.showPts ?? true);
  const [basemap, setBasemap] = useState(memo.basemap ?? true);
  const [search, setSearch] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);
  memo.ds = ds; memo.sel = sel; memo.params = params; memo.showPts = showPts; memo.basemap = basemap;

  const upload = async (file) => {
    if (!file) return;
    setLoading(true); setErr("");
    try {
      const r = await fetch("/api/technical/mapmaking", { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      setDs(await r.json()); setSel([]); setRes(null);
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  // Recompute as soon as the selection or a parameter changes (server caches every tag/parameter combo).
  useEffect(() => {
    if (!ds || !sel.length) { setRes(null); setBusy(false); return undefined; }
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/polygonize", { method: "POST", body: { token: ds.token, tags: sel, ...params } })
        .then((r) => { if (!stale) { setRes(r); setErr(""); setBusy(false); } })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    }, 200);
    return () => { stale = true; clearTimeout(t); };
  }, [ds, sel, params]);

  const pts = useMemo(() => {
    if (!ds || !showPts || !sel.length) return [];
    const colors = new Map(ds.tags.map((t, i) => [i, t.color]));
    const names = new Set(sel);
    const out = [];
    for (const [lat, lng, ids] of ds.points) for (const i of ids) if (names.has(ds.tags[i].name)) out.push({ lat, lng, color: colors.get(i) });
    return out;
  }, [ds, sel, showPts]);

  const shown = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (ds?.tags ?? []).filter((t) => !s || t.name.toLowerCase().includes(s));
  }, [ds, search]);

  const toggle = (n) => setSel((s) => (s.includes(n) ? s.filter((x) => x !== n) : [...s, n]));
  const set = (patch) => setParams((p) => ({ ...p, ...patch }));
  const fc = res?.geojson?.features.length ? res.geojson : null;
  const base = (ds?.name ?? "map").replace(/[^\w-]+/g, "_") + "-polygons";

  const exportAs = async (kind) => {
    try {
      if (kind === "geojson") download(new Blob([toGeoJSON(fc)], { type: "application/geo+json" }), `${base}.geojson`);
      else if (kind === "topojson") download(new Blob([toTopoJSON(fc)], { type: "application/json" }), `${base}.topojson`);
      else if (kind === "svg") download(new Blob([toSVG(fc).svg], { type: "image/svg+xml" }), `${base}.svg`);
      else download(await svgToPng(toSVG(fc)), `${base}.png`);
    } catch (e) { setErr(errorText(e)); }
  };

  return <div className="tp">
    <div className="tp-side">
      <div className={`tp-drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
        <b>mapmaking.app file</b>
        <span className="muted">{loading ? "Reading…" : ds ? `${ds.name} · ${ds.total.toLocaleString()} locations · ${ds.tags.length} tags` : "Drop the exported JSON here"}</span>
        <button type="button" className="btn ghost" disabled={loading} onClick={() => input.current.click()}>{ds ? "Load another" : "Choose file"}</button>
        <input ref={input} type="file" accept=".json,application/json" hidden onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
      </div>

      {ds && <>
        <h3>Tags {sel.length > 0 && <span className="muted">· {sel.length} selected</span>}</h3>
        <input type="search" aria-label="Search tags" placeholder="Search tags" value={search} onChange={(e) => setSearch(e.target.value)} />
        <div className="tp-row">
          <button type="button" className="btn ghost" onClick={() => setSel((s) => [...new Set([...s, ...shown.slice(0, 30).map((t) => t.name)])])}>Add shown (max 30)</button>
          <button type="button" className="btn ghost" disabled={!sel.length} onClick={() => setSel([])}>Clear</button>
        </div>
        <div className="tp-tags">
          {shown.map((t) => <label className="tp-tag" key={t.name} title={t.name}>
            <input type="checkbox" checked={sel.includes(t.name)} onChange={() => toggle(t.name)} />
            <i style={{ background: t.color }} /><span>{t.name}</span><span className="muted">{t.count.toLocaleString()}</span>
          </label>)}
          {!shown.length && <p className="muted">No tag matches.</p>}
        </div>

        <h3>Shape</h3>
        <div className="tp-field"><label htmlFor="tp-method">Method</label>
          <select id="tp-method" value={params.method} onChange={(e) => set({ method: e.target.value })}>
            <option value="blob">Smooth blobs</option><option value="hull">Concave hull</option></select></div>
        <Slider label="Join points closer than" unit=" km" min={5} max={1000} step={5} value={params.link_km} onChange={(v) => set({ link_km: v })} />
        <Slider label="Minimum radius per point" unit=" km" min={0} max={100} step={1} value={params.pad_km} onChange={(v) => set({ pad_km: v })} />
        {params.method === "hull" && <Slider label="Concavity (0 tight, 1 convex)" min={0} max={1} step={0.05} value={params.ratio} onChange={(v) => set({ ratio: v })} />}
        <label className="tp-chk" title="Only countries that have local admin data"><input type="checkbox" checked={params.clip} onChange={(e) => set({ clip: e.target.checked })} />Clip to countries</label>
        <label className="tp-chk"><input type="checkbox" checked={showPts} onChange={(e) => setShowPts(e.target.checked)} />Show places</label>
        <label className="tp-chk"><input type="checkbox" checked={basemap} onChange={(e) => setBasemap(e.target.checked)} />Show basemap</label>

        <h3>Export</h3>
        <div className="tp-row">
          {[["geojson", "GeoJSON"], ["topojson", "TopoJSON"], ["svg", "SVG"], ["png", "Image (PNG)"]].map(([k, l]) =>
            <button key={k} type="button" className="btn ghost" disabled={!fc} onClick={() => exportAs(k)}>{l}</button>)}
        </div>
      </>}
    </div>

    <div className="tp-main">
      {err && <ErrorBox message={err} />}
      {res?.warning && <p className="muted">{res.warning}</p>}
      <PolyMap features={fc} pts={pts} basemap={basemap} fitKey={sel.join("|")} />
      <div className="tp-stats muted">{busy ? "Computing…" : res?.stats.map((s) => `${s.tag}: ${s.polygons} area${s.polygons === 1 ? "" : "s"} from ${s.points} points`).join(" · ")}</div>
    </div>
  </div>;
}
