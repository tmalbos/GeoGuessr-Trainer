import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import Flag from "../../components/Flag.jsx";
import { api, errorText } from "../../lib/api.js";
import { loadWorld } from "../Explore/geo.js";
import { alignedProjection, bboxOf, fitProjection, shapes } from "../Explore/clues/geoDraw.js";
import { download, toGeoJSON } from "./export.js";
import "./technical.css";
import "./stain.css";

const clamp = (v) => Math.min(1, Math.max(0, v));
const MIN_BOX = 0.05; // smallest crop side, as a fraction of the image
const memo = { country: "" }; // remembered while you move between pages

const verdict = (c) => (c >= 0.6 ? ["good", "Map found"] : c >= 0.35 ? ["check", "Check the overlay"] : ["poor", "Not sure it's the map"]);

// "Outline 95% (median 0.4 px, within 1.5 px)": share of the projected line that sits on a drawn edge.
const matchText = (label, m) => `${label} ${Math.round(m.precision * 100)}% (median ${m.median} px, within ${m.tol} px)`;

// The country's own outline and borders (from the app's local maps) as the base, with only the stains on top.
// Both use the same projection, so they always line up.
function StainPreview({ base, name, fc }) {
  const feats = fc?.features ?? [];
  const bb = bboxOf(base.outline);
  if (!bb) return null;
  const W = 1000, H = 700, pad = 8;
  const project = fitProjection(bb, W, H);
  const land = shapes(base.outline, project).polys;
  const lines = base.borders ? shapes(base.borders, project).lines : "";
  return <svg className="sp-svg" viewBox={`${-pad} ${-pad} ${W + pad * 2} ${H + pad * 2}`} role="img" aria-label={`Map of ${name} with the detected stains`}>
    <path className="sp-land" d={land} fillRule="evenodd" />
    {lines && <path className="sp-line" d={lines} fill="none" />}
    {feats.map((f, i) => <path key={i} className="sp-poly" d={shapes(f.geometry, project).polys} fill={f.properties.color} stroke={f.properties.color} fillRule="evenodd"><title>{f.properties.tag}</title></path>)}
  </svg>;
}

export default function StainTool() {
  const [countries, setCountries] = useState([]);
  const [country, setCountry] = useState(memo.country);
  const [url, setUrl] = useState(null);
  const [up, setUp] = useState(null); // { token, width, height, crop }
  const [crop, setCrop] = useState(null);
  const [draft, setDraft] = useState(null);
  const [tol, setTol] = useState(40);
  const [res, setRes] = useState(null);
  const [off, setOff] = useState({}); // hex -> bool, the user's own picks
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);
  const stage = useRef(null);
  const start = useRef(null);
  const draftRef = useRef(null);
  memo.country = country;

  useEffect(() => {
    let alive = true;
    loadWorld().then((w) => alive && setCountries(
      w.countries.map((c) => ({ id: String(c.id), code: String(c.id).toUpperCase(), name: c.name })).sort((a, b) => a.name.localeCompare(b.name)),
    )).catch(() => {});
    return () => { alive = false; };
  }, []);

  const upload = async (file) => {
    if (!file) return;
    setLoading(true); setErr("");
    try {
      const r = await fetch("/api/technical/stain/upload", { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const d = await r.json();
      setUrl((old) => { if (old) URL.revokeObjectURL(old); return URL.createObjectURL(file); });
      setUp(d); setCrop(null); setOff({}); setRes(null);
    } catch (e) { setErr(e.message); } finally { setLoading(false); }
  };

  // Ctrl+V of an image anywhere on the page.
  useEffect(() => {
    const on = (e) => {
      const f = [...(e.clipboardData?.files ?? [])].find((x) => x.type.startsWith("image/"));
      if (f) { e.preventDefault(); upload(f); }
    };
    document.addEventListener("paste", on);
    return () => document.removeEventListener("paste", on);
  }, []);

  // Recompute whenever the image, crop or a setting changes.
  const cropKey = crop ? crop.map((v) => v.toFixed(3)).join(",") : "auto";
  useEffect(() => {
    if (!up || !country) return undefined;
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/stain/analyze", { method: "POST", body: { token: up.token, country, crop, tol } })
        .then((r) => { if (!stale) { setRes(r); setErr(""); setBusy(false); } })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    }, 300);
    return () => { stale = true; clearTimeout(t); };
  }, [up, country, cropKey, tol]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── drawing the crop box on the image ──
  const frac = (e) => {
    const r = stage.current.getBoundingClientRect();
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };
  const onDown = (e) => { e.currentTarget.setPointerCapture(e.pointerId); start.current = frac(e); };
  const onMove = (e) => {
    if (!start.current) return;
    const [x, y] = frac(e);
    const [sx, sy] = start.current;
    draftRef.current = [Math.min(x, sx), Math.min(y, sy), Math.max(x, sx), Math.max(y, sy)];
    setDraft(draftRef.current);
  };
  const onUp = () => {
    const d = draftRef.current;
    start.current = null; draftRef.current = null; setDraft(null);
    if (d && d[2] - d[0] >= MIN_BOX && d[3] - d[1] >= MIN_BOX) setCrop(d);
  };

  // Only stains come back (the base map is the app's own). Every stain starts on.
  const on = (hex) => off[hex] ?? true;
  const fc = useMemo(() => {
    if (!res) return null;
    const keep = res.geojson.features.filter((f) => on(f.properties.hex));
    return keep.length ? { type: "FeatureCollection", features: keep } : null;
  }, [res, off]); // eslint-disable-line react-hooks/exhaustive-deps

  const box = draft ?? crop ?? res?.bbox ?? null;
  const [vk, vl] = res ? verdict(res.fit.confidence) : [];
  const noStains = !!res && !res.classes.length;

  return <div className="tp st">
    <div className="tp-side">
      <div className={`tp-drop ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
        <b>Map screenshot</b>
        <span className="muted">{loading ? "Reading…" : up ? `${up.width}×${up.height} px` : "Drop an image, or press Ctrl+V"}</span>
        <button type="button" className="btn ghost" disabled={loading} onClick={() => input.current.click()}>{up ? "Load another" : "Choose file"}</button>
        <input ref={input} type="file" accept="image/*" hidden onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
      </div>

      <h3>Country</h3>
      <div className="tp-row">
        {country && <Flag code={country} width={28} />}
        <select aria-label="Country" value={country} onChange={(e) => setCountry(e.target.value)}>
          <option value="">Pick the country on the image</option>
          {countries.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
        </select>
      </div>

      <h3>Matching</h3>
      <div className="tp-field">
        <label htmlFor="st-tol" title="Shades closer than this are merged into one stain">Merge similar shades<span className="muted">{tol}</span></label>
        <input id="st-tol" type="range" min={10} max={100} step={5} value={tol} onChange={(e) => setTol(Number(e.target.value))} />
      </div>

      {res && <>
        <h3>Stains found</h3>
        {noStains ? <p className="muted">No stained areas found inside the map.</p> : <div className="tp-tags">
          {res.classes.map((c) => <label className="tp-tag" key={c.hex} title={c.hex}>
            <input type="checkbox" checked={on(c.hex)} onChange={() => setOff((o) => ({ ...o, [c.hex]: on(c.hex) }))} />
            <i style={{ background: c.hex }} /><span>{c.tag}</span><span className="muted">{Math.round(c.share * 100)}%</span>
          </label>)}
        </div>}
        <h3>Export</h3>
        <div className="tp-row">
          <button type="button" className="btn ghost" disabled={!fc}
            onClick={() => download(new Blob([toGeoJSON(fc)], { type: "application/geo+json" }), `${country}-stain.geojson`)}>GeoJSON</button>
        </div>
      </>}
    </div>

    <div className="tp-main">
      {err && <ErrorBox message={err} />}
      {!up && <p className="muted">Load a screenshot and pick the country. The map is found automatically; draw a box on the image only if it picks the wrong thing.</p>}
      {up && url && <div className="st-grid">
        <div className="st-left">
          <div className="st-stage" ref={stage} onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
            <img src={url} alt="Uploaded map screenshot" draggable={false} />
            {res && <svg className="st-ov" viewBox={`0 0 ${res.size[0]} ${res.size[1]}`} preserveAspectRatio="none" aria-hidden="true">
              <path className="o" d={res.overlay.outline} /><path className="b" d={res.overlay.borders} />
            </svg>}
            {box && <div className="st-crop" style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%` }} />}
          </div>
          <div className="st-bar">
            <button type="button" className="btn ghost" disabled={!crop} onClick={() => setCrop(null)}>Auto (search the whole image)</button>
            {res && <span className="st-legend"><span><i />Country outline, placed</span>{res.fit.borders != null && <span><i className="b" />Known admin borders</span>}</span>}
          </div>
          <div className="st-status">
            {!country ? <span className="muted">Pick the country to match against.</span>
              : busy ? <span className="muted">Computing…</span>
              : res ? <span><b className={vk}>{vl}</b>
                {res.fit.outline_match
                  ? <> · {matchText("Outline", res.fit.outline_match)}{res.fit.borders_match && <> · {matchText("Regions", res.fit.borders_match)}</>}</>
                  : <> · outline {Math.round(res.fit.outline * 100)}%{res.fit.borders != null && ` · borders ${Math.round(res.fit.borders * 100)}%`}</>}
                {" "}· {res.fit.projection} · aspect {res.fit.aspect.toFixed(2)}
                {res.fit.confidence < 0.35 && <span className="muted"> · draw a box around the map to help the search</span>}</span> : null}
          </div>
        </div>
        {res && <div className="st-right">
          <StainPreview base={res.base} name={countries.find((c) => c.code === country)?.name ?? country} fc={fc} />
        </div>}
      </div>}
    </div>
  </div>;
}
