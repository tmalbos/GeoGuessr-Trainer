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
const MIN_ZOOM = 1;
const MAX_ZOOM = 8;
const memo = { country: "" }; // remembered while you move between pages

// Wheel to zoom around the cursor, drag (on the caller's side) to pan. 1x is "fit".
function useViewZoom(ref, ready = true) {
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });
  useEffect(() => {
    const el = ready ? ref.current : null;
    if (!el) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect();
      const mx = e.clientX - r.left, my = e.clientY - r.top;
      setView((v) => {
        const s = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.s * (e.deltaY < 0 ? 1.18 : 1 / 1.18)));
        if (s === v.s) return v;
        const px = (mx - v.x) / v.s, py = (my - v.y) / v.s;
        return { s, x: mx - px * s, y: my - py * s };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [ref, ready]);
  const reset = () => setView({ s: 1, x: 0, y: 0 });
  const zoomBy = (k) => {
    const el = ref.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const mx = r.width / 2, my = r.height / 2;
    setView((v) => {
      const s = Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, v.s * k));
      const px = (mx - v.x) / v.s, py = (my - v.y) / v.s;
      return { s, x: mx - px * s, y: my - py * s };
    });
  };
  return { view, setView, reset, zoomBy };
}

const viewStyle = (v) => ({ transform: `translate(${v.x}px, ${v.y}px) scale(${v.s})` });

function ViewBar({ view, onZoom, onReset }) {
  return <span className="st-view">
    <button type="button" className="btn ghost" title="Zoom out" onClick={() => onZoom(1 / 1.3)}>−</button>
    <span className="muted">{Math.round(view.s * 100)}%</span>
    <button type="button" className="btn ghost" title="Zoom in" onClick={() => onZoom(1.3)}>+</button>
    <button type="button" className="btn ghost" disabled={view.s === 1 && view.x === 0 && view.y === 0} onClick={onReset}>Reset view</button>
  </span>;
}

const verdict = (c) => (c >= 0.6 ? ["good", "Map found"] : c >= 0.35 ? ["check", "Check the overlay"] : ["poor", "Not sure it's the map"]);

// "Outline 95% on an edge (median 0.4 px, p90 1.1 px; 6.2 px if shifted; 63% of nearby edges explained)":
// share of the line within `tol` px of an image edge that runs the same way, how far the line is from it,
// the same distance for the line shifted a few px (chance level), and the share of edges next to the line it explains.
const matchText = (label, m) => {
  let t = `${label} ${Math.round(m.precision * 100)}% on an edge (median ${m.median} px, p90 ${m.p90} px`;
  if (m.chance_median != null) t += `; ${m.chance_median} px if shifted`;
  t += ")";
  if (m.reverse != null) t += ` · ${Math.round(m.reverse * 100)}% of nearby edges explained`;
  return t;
};

const pct = (v) => (typeof v === "number" ? `${Math.round(v * 100)}%` : "—");
const px = (v) => (typeof v === "number" ? `${v.toFixed(2)} px` : "—");

// The country's own outline and borders (from the app's local maps) as the base, with only the stains on top.
// Both use the same projection, so they always line up. Wheel zooms, left-drag pans.
function StainPreview({ base, name, fc }) {
  const vp = useRef(null);
  const { view, setView, reset, zoomBy } = useViewZoom(vp);
  const pan = useRef(null);
  const feats = fc?.features ?? [];
  const bb = bboxOf(base.outline);
  if (!bb) return null;
  const W = 1000, H = 700, pad = 8;
  const project = fitProjection(bb, W, H);
  const land = shapes(base.outline, project).polys;
  const lines = base.borders ? shapes(base.borders, project).lines : "";
  const onDown = (e) => {
    if (e.button !== 0) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    pan.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
  };
  const onMove = (e) => {
    const p = pan.current;
    if (!p) return;
    setView((v) => ({ ...v, x: p.vx + e.clientX - p.x, y: p.vy + e.clientY - p.y }));
  };
  const onUp = () => { pan.current = null; };
  return <div className="sp-wrap">
    <div className="sp-bar">
      <span className="muted">Map preview</span>
      <ViewBar view={view} onZoom={zoomBy} onReset={reset} />
    </div>
    <div className="sp-viewport" ref={vp} onPointerDown={onDown} onPointerMove={onMove}
      onPointerUp={onUp} onPointerCancel={onUp} onContextMenu={(e) => e.preventDefault()}>
      <div className="sp-zoom" style={viewStyle(view)}>
        <svg className="sp-svg" viewBox={`${-pad} ${-pad} ${W + pad * 2} ${H + pad * 2}`} role="img" aria-label={`Map of ${name} with the detected stains`}>
          <path className="sp-land" d={land} fillRule="evenodd" />
          {lines && <path className="sp-line" d={lines} fill="none" />}
          {feats.map((f, i) => <path key={i} className="sp-poly" d={shapes(f.geometry, project).polys} fill={f.properties.color} stroke={f.properties.color} fillRule="evenodd"><title>{f.properties.tag}</title></path>)}
        </svg>
      </div>
    </div>
  </div>;
}

export default function StainTool() {
  const [countries, setCountries] = useState([]);
  const [country, setCountry] = useState(memo.country);
  const [url, setUrl] = useState(null);
  const [up, setUp] = useState(null); // { token, width, height, map: { found, boxes, ms } }
  const [crop, setCrop] = useState(null);
  const [draft, setDraft] = useState(null);
  const [tol, setTol] = useState(40);
  const [res, setRes] = useState(null);
  const [off, setOff] = useState({}); // hex -> bool, the user's own picks
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);
  const [err, setErr] = useState("");
  const [warpMode, setWarpMode] = useState(false);
  const [warp, setWarp] = useState({ sx: 1, sy: 1, dx: 0, dy: 0 });
  const [warpRes, setWarpRes] = useState(null);
  const [warpBusy, setWarpBusy] = useState(false);
  const [warpErr, setWarpErr] = useState("");
  const [scanBusy, setScanBusy] = useState(false);
  const [scanErr, setScanErr] = useState("");
  const input = useRef(null);
  const stage = useRef(null);
  const start = useRef(null);
  const draftRef = useRef(null);
  const warpDrag = useRef(null);
  const pan = useRef(null);
  const { view, setView, reset: resetView, zoomBy: zoomView } = useViewZoom(stage, !!url);
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
      setWarpMode(false); setWarp({ sx: 1, sy: 1, dx: 0, dy: 0 }); setWarpRes(null); setWarpErr("");
      setScanBusy(false); setScanErr("");
      resetView();
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

  // No map detected in the image (and no box drawn by the user): the matching step is not run.
  const det = up?.map; // generic "is there a map in this image, and where" (no country needed)
  const noMap = !!up && det?.found === false && !crop;

  // Recompute whenever the image, crop or a setting changes.
  const cropKey = crop ? crop.map((v) => v.toFixed(3)).join(",") : "auto";
  useEffect(() => {
    if (!up || !country) return undefined;
    if (noMap) { setRes(null); setBusy(false); return undefined; } // nothing to match against
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      api("/technical/stain/analyze", { method: "POST", body: { token: up.token, country, crop, tol } })
        .then((r) => { if (!stale) { setRes(r); setErr(""); setBusy(false); } })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setBusy(false); } });
    }, 300);
    return () => { stale = true; clearTimeout(t); };
  }, [up, country, cropKey, tol, noMap]); // eslint-disable-line react-hooks/exhaustive-deps

  // A new image/country/crop invalidates a manual fit.
  useEffect(() => {
    setWarp({ sx: 1, sy: 1, dx: 0, dy: 0 });
    setWarpRes(null);
    setWarpErr("");
  }, [up, country, cropKey]);

  // Rescore the warped outline against the same edge maps the automatic fit used.
  useEffect(() => {
    if (!warpMode) { setWarpBusy(false); return undefined; }
    if (busy || !res?.fit?.box || !up || !country) { setWarpBusy(false); return undefined; }
    let stale = false;
    const [px, py] = [(res.fit.box[0] + res.fit.box[2]) / 2, (res.fit.box[1] + res.fit.box[3]) / 2];
    setWarpBusy(true);
    const t = setTimeout(() => {
      api("/technical/stain/warp", { method: "POST", body: {
        token: up.token, country, crop, sx: warp.sx, sy: warp.sy, dx: warp.dx, dy: warp.dy, pivot: [px, py],
      } }).then((r) => {
        if (!stale) { setWarpRes(r); setWarpErr(""); setWarpBusy(false); }
      }).catch((e) => {
        if (!stale) { setWarpErr(errorText(e)); setWarpBusy(false); }
      });
    }, 250);
    return () => { stale = true; clearTimeout(t); };
  }, [warpMode, warp, up, country, cropKey, res, busy]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── drawing the crop box, panning, or dragging the outline in manual mode ──
  const frac = (e) => {
    const r = stage.current.getBoundingClientRect();
    return [
      clamp(((e.clientX - r.left) - view.x) / (r.width * view.s)),
      clamp(((e.clientY - r.top) - view.y) / (r.height * view.s)),
    ];
  };
  const workDelta = (dx, dy) => {
    const r = stage.current.getBoundingClientRect();
    return [(dx * res.size[0]) / (r.width * view.s), (dy * res.size[1]) / (r.height * view.s)];
  };
  const onDown = (e) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    if (e.button === 1 || e.button === 2) {
      pan.current = { x: e.clientX, y: e.clientY, vx: view.x, vy: view.y };
      return;
    }
    if (e.button !== 0) return;
    if (warpMode && res) {
      warpDrag.current = { x: e.clientX, y: e.clientY, dx: warp.dx, dy: warp.dy };
      return;
    }
    start.current = frac(e);
  };
  const onMove = (e) => {
    if (pan.current) {
      const p = pan.current;
      setView((v) => ({ ...v, x: p.vx + e.clientX - p.x, y: p.vy + e.clientY - p.y }));
      return;
    }
    if (warpDrag.current) {
      const d = warpDrag.current;
      const [dx, dy] = workDelta(e.clientX - d.x, e.clientY - d.y);
      setWarp((w) => ({ ...w, dx: d.dx + dx, dy: d.dy + dy }));
      return;
    }
    if (!start.current) return;
    const [x, y] = frac(e);
    const [sx, sy] = start.current;
    draftRef.current = [Math.min(x, sx), Math.min(y, sy), Math.max(x, sx), Math.max(y, sy)];
    setDraft(draftRef.current);
  };
  const onUp = () => {
    if (pan.current) { pan.current = null; return; }
    if (warpDrag.current) { warpDrag.current = null; return; }
    const d = draftRef.current;
    start.current = null; draftRef.current = null; setDraft(null);
    if (d && d[2] - d[0] >= MIN_BOX && d[3] - d[1] >= MIN_BOX) setCrop(d);
  };
  const toggleWarp = () => setWarpMode((v) => !v);

  const scanReport = async () => {
    if (!up || !res?.fit?.box || !country) return;
    setScanBusy(true); setScanErr("");
    const [cx, cy] = [(res.fit.box[0] + res.fit.box[2]) / 2, (res.fit.box[1] + res.fit.box[3]) / 2];
    try {
      const r = await fetch("/api/technical/stain/scan", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        token: up.token, country, crop, sx: warp.sx, sy: warp.sy, dx: warp.dx, dy: warp.dy,
        pivot: [cx, cy], span_pct: 2, span_px: 2, step_pct: 0.1, step_px: 0.1,
      }) });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      download(await r.blob(), `${country}-stain-scan.zip`);
    } catch (e) { setScanErr(errorText(e)); } finally { setScanBusy(false); }
  };

  // Only stains come back (the base map is the app's own). Every stain starts on.
  const on = (hex) => off[hex] ?? true;
  const fc = useMemo(() => {
    if (!res) return null;
    const keep = res.geojson.features.filter((f) => on(f.properties.hex));
    return keep.length ? { type: "FeatureCollection", features: keep } : null;
  }, [res, off]); // eslint-disable-line react-hooks/exhaustive-deps

  // What is outlined on the image: the box being drawn, the user's crop, the placed country, or (right after the
  // upload) the map the detector found.
  const box = draft ?? crop ?? res?.bbox ?? (det?.found ? det.boxes[0].box : null);
  const [vk, vl] = res ? verdict(res.fit.confidence) : [];
  const noStains = !!res && !res.classes.length;
  const fitBox = res?.fit?.box;
  const pivot = fitBox ? [(fitBox[0] + fitBox[2]) / 2, (fitBox[1] + fitBox[3]) / 2] : [0, 0];
  const warpXf = warpMode && fitBox
    ? `translate(${warp.dx} ${warp.dy}) translate(${pivot[0]} ${pivot[1]}) scale(${warp.sx} ${warp.sy}) translate(${-pivot[0]} ${-pivot[1]})`
    : undefined;
  const bw = fitBox ? fitBox[2] - fitBox[0] : 100;
  const bh = fitBox ? fitBox[3] - fitBox[1] : 100;
  const dxMax = Math.max(40, Math.round(bw * 0.4), Math.abs(warp.dx) + 1);
  const dyMax = Math.max(40, Math.round(bh * 0.4), Math.abs(warp.dy) + 1);
  const shift = (v) => `${Number(v.toFixed(1))} px`;
  const bump = (key, delta, lo, hi) => setWarp((w) => ({
    ...w,
    [key]: Math.min(hi, Math.max(lo, Number((w[key] + delta).toFixed(4)))),
  }));
  const ra = res?.fit?.regions;
  const rm = warpRes?.fit?.regions;
  const cmp = res && warpRes ? [
    ["Outline edge", pct(res.fit.outline_match?.precision), pct(warpRes.fit.outline_match?.precision)],
    ["Outline median", px(res.fit.outline_match?.median), px(warpRes.fit.outline_match?.median)],
    ["Borders edge", pct(res.fit.borders_match?.precision), pct(warpRes.fit.borders_match?.precision)],
    ["Borders median", px(res.fit.borders_match?.median), px(warpRes.fit.borders_match?.median)],
    ["NW region edge", pct(ra?.NW?.precision), pct(rm?.NW?.precision)],
    ["NE region edge", pct(ra?.NE?.precision), pct(rm?.NE?.precision)],
    ["SW region edge", pct(ra?.SW?.precision), pct(rm?.SW?.precision)],
    ["SE region edge", pct(ra?.SE?.precision), pct(rm?.SE?.precision)],
  ] : [];

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
          <div className={`st-stage ${warpMode ? "warp" : ""}`} ref={stage} onPointerDown={onDown}
            onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp} onContextMenu={(e) => e.preventDefault()}>
            <div className="st-zoom" style={viewStyle(view)}>
              <img src={url} alt="Uploaded map screenshot" draggable={false} />
              {res && <svg className="st-ov" viewBox={`0 0 ${res.size[0]} ${res.size[1]}`} preserveAspectRatio="none" aria-hidden="true">
                <g transform={warpXf}>
                  <path className="o" d={res.overlay.outline} /><path className="b" d={res.overlay.borders} />
                </g>
              </svg>}
              {box && <div className="st-crop" style={{ left: `${box[0] * 100}%`, top: `${box[1] * 100}%`, width: `${(box[2] - box[0]) * 100}%`, height: `${(box[3] - box[1]) * 100}%` }} />}
            </div>
          </div>
          {det && det.found != null && <p className="st-detect">
            {det.found
              ? <><b className="yes">Map detected</b> <span className="muted">· outlined on the image, used to guide the search · {det.ms} ms</span></>
              : <><b className="no">No map detected</b> <span className="muted">· {det.ms} ms. Matching is skipped; draw a box around the map to search anyway.</span></>}
          </p>}
          <div className="st-bar">
            <button type="button" className="btn ghost" disabled={!crop} onClick={() => setCrop(null)}>Auto (search the whole image)</button>
            <button type="button" className="btn ghost" aria-pressed={warpMode} disabled={!res}
              onClick={toggleWarp}>{warpMode ? "Manual warp: on" : "Manual warp"}</button>
            <ViewBar view={view} onZoom={zoomView} onReset={resetView} />
            {res && <span className="st-legend"><span><i />Country outline, placed</span>{res.fit.borders != null && <span><i className="b" />Known admin borders</span>}</span>}
          </div>
          <p className="st-hint muted">Wheel to zoom · right-drag to pan{warpMode ? " · drag the outline to move it" : ""}</p>
          <div className="st-status">
            {noMap ? <span className="muted">No map in this image, so there is nothing to match. Draw a box around it to try anyway.</span>
              : !country ? <span className="muted">Pick the country to match against.</span>
              : busy ? <span className="muted">Computing…</span>
              : res ? <span><b className={vk}>{vl}</b>
                {res.fit.outline_match
                  ? <> · {matchText("Outline", res.fit.outline_match)}{res.fit.borders_match && <> · {matchText("Regions", res.fit.borders_match)}</>}</>
                  : <> · outline {Math.round(res.fit.outline * 100)}%{res.fit.borders != null && ` · borders ${Math.round(res.fit.borders * 100)}%`}</>}
                {" "}· {res.fit.projection} · aspect {res.fit.aspect.toFixed(2)}
                {Math.abs(res.fit.rotation_deg) >= 0.1 && ` · rotated ${res.fit.rotation_deg}°`}
                {Math.abs(res.fit.skew_deg) >= 0.1 && ` · skew ${res.fit.skew_deg}°`}
                {res.fit.confidence < 0.35 && <span className="muted"> · draw a box around the map to help the search</span>}</span> : null}
          </div>

          {res && <section className="st-warp" aria-label="Manual fit">
            <div className="st-warp-head">
              <h3>Manual fit</h3>
              <button type="button" className="btn ghost" aria-pressed={warpMode} onClick={toggleWarp}>
                {warpMode ? "Warp on" : "Try a manual warp"}</button>
            </div>
            {!warpMode && <p className="muted">Move and scale the placed outline by hand. It applies an extra independent X/Y scale and
              translation on top of the automatic fit, and rescoring tells us whether the search or the metric is at fault.</p>}
            {warpMode && <>
              <div className="st-warp-grid">
                <label>
                  <span>Scale X</span>
                  <span className="st-step">
                    <button type="button" className="btn ghost" title="0.1% smaller" onClick={() => bump("sx", -0.001, 0.7, 1.4)}>−</button>
                    <b>{(warp.sx * 100).toFixed(1)}%</b>
                    <button type="button" className="btn ghost" title="0.1% larger" onClick={() => bump("sx", 0.001, 0.7, 1.4)}>+</button>
                  </span>
                  <input type="range" min={0.7} max={1.4} step={0.001} value={warp.sx}
                    onChange={(e) => setWarp((w) => ({ ...w, sx: Number(e.target.value) }))} />
                </label>
                <label>
                  <span>Scale Y</span>
                  <span className="st-step">
                    <button type="button" className="btn ghost" title="0.1% smaller" onClick={() => bump("sy", -0.001, 0.7, 1.4)}>−</button>
                    <b>{(warp.sy * 100).toFixed(1)}%</b>
                    <button type="button" className="btn ghost" title="0.1% larger" onClick={() => bump("sy", 0.001, 0.7, 1.4)}>+</button>
                  </span>
                  <input type="range" min={0.7} max={1.4} step={0.001} value={warp.sy}
                    onChange={(e) => setWarp((w) => ({ ...w, sy: Number(e.target.value) }))} />
                </label>
                <label>
                  <span>Shift X</span>
                  <span className="st-step">
                    <button type="button" className="btn ghost" title="0.1 px left" onClick={() => bump("dx", -0.1, -dxMax, dxMax)}>−</button>
                    <b>{shift(warp.dx)}</b>
                    <button type="button" className="btn ghost" title="0.1 px right" onClick={() => bump("dx", 0.1, -dxMax, dxMax)}>+</button>
                  </span>
                  <input type="range" min={-dxMax} max={dxMax} step={0.1} value={warp.dx}
                    onChange={(e) => setWarp((w) => ({ ...w, dx: Number(e.target.value) }))} />
                </label>
                <label>
                  <span>Shift Y</span>
                  <span className="st-step">
                    <button type="button" className="btn ghost" title="0.1 px up" onClick={() => bump("dy", -0.1, -dyMax, dyMax)}>−</button>
                    <b>{shift(warp.dy)}</b>
                    <button type="button" className="btn ghost" title="0.1 px down" onClick={() => bump("dy", 0.1, -dyMax, dyMax)}>+</button>
                  </span>
                  <input type="range" min={-dyMax} max={dyMax} step={0.1} value={warp.dy}
                    onChange={(e) => setWarp((w) => ({ ...w, dy: Number(e.target.value) }))} />
                </label>
              </div>
              <div className="st-warp-actions">
                <button type="button" className="btn ghost" onClick={() => setWarp({ sx: 1, sy: 1, dx: 0, dy: 0 })}>Reset warp</button>
                <button type="button" className="btn ghost" disabled={scanBusy || !res?.fit?.box}
                  title="Absolute grid: Scale X/Y 98–102%, Shift X/Y −2..+2, step 0.1; 2,825,761 configurations, about 13 minutes"
                  onClick={scanReport}>{scanBusy ? "Scanning absolute ±2%…" : "Download absolute ±2% scan"}</button>
                {warpBusy ? <span className="muted">Scoring…</span> : warpRes ? <span className="muted">Scored</span> : null}
              </div>
              {scanErr && <p className="st-warn">{scanErr}</p>}
              {scanBusy && <p className="muted">Building the full grid. Keep this tab open; it can take several minutes.</p>}
              {warpErr && <p className="st-warn">{warpErr}</p>}
              {!!cmp.length && <table className="st-cmp">
                <thead><tr><th>Metric</th><th>Auto</th><th>Manual</th></tr></thead>
                <tbody>{cmp.map(([label, a, m]) => <tr key={label}><td>{label}</td><td>{a}</td><td>{m}</td></tr>)}</tbody>
              </table>}
              <p className="muted">Only the outline is rescored; the stains and GeoJSON still use the automatic fit.</p>
            </>}
          </section>}
        </div>
        {res && <div className="st-right">
          <StainPreview key={up.token} base={res.base} name={countries.find((c) => c.code === country)?.name ?? country} fc={fc} />
        </div>}
      </div>}
    </div>
  </div>;
}
