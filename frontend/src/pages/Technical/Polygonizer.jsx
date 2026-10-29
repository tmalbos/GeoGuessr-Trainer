import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { download, svgToPng, toGeoJSON, toSVG, toTopoJSON } from "./export.js";
import PolyMap from "./PolyMap.jsx";
import ShapeControls, { SHAPE_DEFAULTS } from "./ShapeControls.jsx";
import "./technical.css";

const memo = {}; // survives leaving the page: the uploaded file lives on the server, this remembers which one

export default function Polygonizer() {
  const [ds, setDs] = useState(memo.ds ?? null); // { token, name, total, tags, points }
  const [sel, setSel] = useState(memo.sel ?? []);
  const [params, setParams] = useState({ ...SHAPE_DEFAULTS, ...memo.params });
  const [colors, setColors] = useState(memo.colors ?? {}); // tag name -> custom colour
  const [search, setSearch] = useState("");
  const [res, setRes] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [picker, setPicker] = useState(null); // { kind, countries, code } while the user picks a country for an image
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);
  memo.ds = ds; memo.sel = sel; memo.params = params; memo.colors = colors;

  const upload = async (file) => {
    if (!file) return;
    setLoading(true); setErr("");
    try {
      const r = await fetch("/api/technical/mapmaking", { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      setDs(await r.json()); setSel([]); setRes(null); setColors({}); setPicker(null);
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  // Recompute as soon as the selection or a parameter changes (server caches every tag/parameter combo).
  useEffect(() => {
    if (!ds || !sel.length) { setRes(null); setBusy(false); return undefined; }
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/polygonize", { method: "POST", body: { token: ds.token, tags: sel, ...params } })
        .then((r) => { if (!stale) { setRes({ ...r, key: sel.join("|") }); setErr(""); setBusy(false); } })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    }, 200);
    return () => { stale = true; clearTimeout(t); };
  }, [ds, sel, params]);

  const colorOf = (t) => colors[t.name] ?? t.color;

  const recolor = (collection) => ({
    ...collection,
    features: collection.features.map((f) => ({ ...f, properties: { ...f.properties, color: colors[f.properties.tag] ?? f.properties.color } })),
  });

  const shown = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (ds?.tags ?? []).filter((t) => !s || t.name.toLowerCase().includes(s));
  }, [ds, search]);

  const toggle = (n) => setSel((s) => (s.includes(n) ? s.filter((x) => x !== n) : [...s, n]));
  const fc = useMemo(() => (res?.geojson?.features.length ? recolor(res.geojson) : null), [res, colors]); // eslint-disable-line react-hooks/exhaustive-deps
  const base = (ds?.name ?? "map").replace(/[^\w-]+/g, "_") + "-polygons";

  // Images are drawn on top of their country. One country: used straight away. Several: the user picks.
  const exportImage = async (kind, code) => {
    const body = { token: ds.token, tags: sel, ...params };
    let c = code;
    if (!c) {
      const { countries } = await api("/technical/polygonize/base", { method: "POST", body });
      if (!countries.length) throw new Error("No local country outline covers these tags, so there is no country to draw them on.");
      if (countries.length > 1) { setPicker({ kind, countries, code: countries[0].code }); return; }
      c = countries[0].code;
    }
    setPicker(null);
    const { outline, borders, features } = await api("/technical/polygonize/base", { method: "POST", body: { ...body, code: c } });
    if (!features.features.length) throw new Error("Nothing of these tags falls inside that country.");
    const img = toSVG(recolor(features), { base: outline, borders });
    download(kind === "svg" ? new Blob([img.svg], { type: "image/svg+xml" }) : await svgToPng(img), `${base}-${c}.${kind}`);
  };

  const exportAs = async (kind, code) => {
    setExporting(true);
    try {
      if (kind === "geojson") download(new Blob([toGeoJSON(fc)], { type: "application/geo+json" }), `${base}.geojson`);
      else if (kind === "topojson") download(new Blob([toTopoJSON(fc)], { type: "application/json" }), `${base}.topojson`);
      else await exportImage(kind, code);
    } catch (e) { setErr(errorText(e)); } finally { setExporting(false); }
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
            <input type="color" aria-label={`Color of ${t.name}`} title="Change color" value={colorOf(t)}
              onChange={(e) => setColors((c) => ({ ...c, [t.name]: e.target.value }))} />
            <span>{t.name}</span><span className="muted">{t.count.toLocaleString()}</span>
          </label>)}
          {!shown.length && <p className="muted">No tag matches.</p>}
        </div>

        <h3>Shape</h3>
        <ShapeControls params={params} onChange={setParams} />

        <h3>Export</h3>
        <div className="tp-row">
          {[["geojson", "GeoJSON"], ["topojson", "TopoJSON"], ["svg", "SVG"], ["png", "Image (PNG)"]].map(([k, l]) =>
            <button key={k} type="button" className="btn ghost" disabled={!fc || exporting} onClick={() => exportAs(k)}>{l}</button>)}
        </div>
        {picker && <div className="tp-field">
          <label htmlFor="tp-country">These tags span several countries. Which one to export?</label>
          <select id="tp-country" value={picker.code} onChange={(e) => setPicker((p) => ({ ...p, code: e.target.value }))}>
            {picker.countries.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
          </select>
          <div className="tp-row">
            <button type="button" className="btn" disabled={exporting} onClick={() => exportAs(picker.kind, picker.code)}>Export</button>
            <button type="button" className="btn ghost" onClick={() => setPicker(null)}>Cancel</button>
          </div>
        </div>}
      </>}
    </div>

    <div className="tp-main">
      {err && <ErrorBox message={err} />}
      {res?.warning && <p className="muted">{res.warning}</p>}
      <PolyMap features={fc} fitKey={res?.key} />
      {busy && <div className="tp-stats muted">Computing…</div>}
    </div>
  </div>;
}
