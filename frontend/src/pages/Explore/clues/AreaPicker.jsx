import { useEffect, useRef, useState } from "react";
import { api, errorText } from "../../../lib/api.js";
import { discardStaged, stagePath, stagedUrl } from "./imageTools.js";
import { parseStreetView, streetViewShot } from "./mediaKinds.js";
import FilePicker from "./FilePicker.jsx";
import ShapeControls, { SHAPE_DEFAULTS } from "../../Technical/ShapeControls.jsx";

const ACCEPT = ".svg,.csv,.geojson,.json,.gpkg,.zip,.parquet,.kml,image/*";
const HINT = "The part of the country the clue applies to: an SVG map, a CSV of subdivisions, a geometry file, a mapmaking.app export (one tag becomes the region), or a picture of the map (the areas are then picked out of it)";
const MIN_BOX = 0.04;

const clamp = (v) => Math.min(1, Math.max(0, v));
const rect = (r) => r && { left: `${r[0] * 100}%`, top: `${r[1] * 100}%`, width: `${(r[2] - r[0]) * 100}%`, height: `${(r[3] - r[1]) * 100}%` };

// The clue map draws one hue, lighter for rarer tones. Rank the stains by how light they are found in the
// picture, so two stains of different intensity become the main tone and its lighter equivalent.
const lum = (hex) => {
  const m = /^#?([0-9a-f]{6})$/i.exec(String(hex ?? ""));
  if (!m) return 0;
  const n = parseInt(m[1], 16);
  return 0.299 * ((n >> 16) & 255) + 0.587 * ((n >> 8) & 255) + 0.114 * (n & 255);
};
const withShades = (feats) => {
  const order = feats.map((f, i) => [i, lum(f.properties?.hex ?? f.properties?.color)])
    .sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const shades = new Array(feats.length);
  order.forEach(([i], rank) => { shades[i] = Math.min(4, rank); });
  return feats.map((f, i) => ({ ...f, properties: { ...f.properties, shade: shades[i] } }));
};

/** The region slot below the media: an SVG, CSV or geometry file, or a picture of the map, which is
 *  run through the stain tool for this country. The result is shown once, on the country (FilePicker's preview).
 *  The picture itself only appears when the map has to be boxed by hand (not found / low confidence) or on request.
 *  A mapmaking.app export dropped / pasted / browsed into the same box is recognised: its locations inside this
 *  country are listed by tag, one tag becomes the region (shape tuned under "Customize"). */
export default function AreaPicker({ cc, value, current, onChange, stage, initialPicture }) {
  const [up, setUp] = useState(null);       // { token, url, map } — the picture sent to the stain tool
  const [crop, setCrop] = useState(null);   // [x0, y0, x1, y1] as fractions of the picture, drawn by hand
  const [res, setRes] = useState(null);     // stain result
  const [draft, setDraft] = useState(null);
  const [adjust, setAdjust] = useState(false); // user asked to see the picture
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const shot = useRef(null);
  const start = useRef(null);
  const drag = useRef(null);
  const staged = useRef(null);              // staged geometry from the last run (thrown away when redone)
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const changeRef = useRef(onChange);
  changeRef.current = onChange;
  const [mm, setMm] = useState(null);       // mapmaking export: { token, name, total, outside, tags }
  const [sel, setSel] = useState("");       // the one tag picked
  const [params, setParams] = useState(SHAPE_DEFAULTS);
  const [mmBusy, setMmBusy] = useState(false);
  const [mmErr, setMmErr] = useState("");

  const strip = (s) => String(s ?? "").trim().replace(/^"|"$/g, "");

  const pictureOf = async (input) => {
    if (input.file) return input.file;
    const raw = strip(input.text ?? input.path);
    const link = parseStreetView(raw);
    if (!link) return null; // a local path: the server reads it
    if (link.short) throw new Error("Short Google Maps links can't be read. Open the link and copy the full address.");
    return streetViewShot(link);
  };

  const upload = async (file, path) => {
    const qs = file ? "" : `?path=${encodeURIComponent(path)}`;
    const r = await fetch(`/api/technical/stain/upload${qs}`, file ? { method: "POST", body: file } : { method: "POST" });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
    return r.json();
  };

  const takePicture = async (input) => {
    setBusy(true); setErr(""); setRes(null); setCrop(null); setAdjust(false);
    try {
      const file = await pictureOf(input);
      let preview;
      if (file) preview = { url: URL.createObjectURL(file) };
      else {
        const stagedPicture = await stagePath(strip(input.text ?? input.path));
        preview = { url: stagedUrl(stagedPicture.token), token: stagedPicture.token };
      }
      const d = await upload(file, strip(input.text ?? input.path));
      if (shot.current) {
        if (shot.current.url?.startsWith("blob:")) URL.revokeObjectURL(shot.current.url);
        discardStaged(shot.current.token);
      }
      shot.current = preview;
      setUp({ ...d, url: preview.url });
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };

  // Imported clue: the picture Plonkit shows is run through the stain tool right away.
  useEffect(() => { if (initialPicture) takePicture({ file: initialPicture }); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // A .json dropped into the Region box may be a mapmaking export: then it is handled here (undefined = "handled",
  // FilePicker leaves the current region alone). Anything else goes on to the normal geometry staging.
  const stageArea = async (input) => {
    const path = strip(input.path ?? input.text);
    if (/\.json$/i.test(input.file ? input.file.name : path)) {
      const r = await fetch(`/api/technical/mapmaking/country/${cc.toUpperCase()}${input.file ? "" : `?path=${encodeURIComponent(path)}`}`,
        input.file ? { method: "POST", body: input.file } : { method: "POST" });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const d = await r.json();
      if (d.mapmaking) { setMm(d); setSel(""); setMmErr(""); return undefined; }
    }
    return stage(input);
  };

  // Draw the picked tag's shape and set it as the region (same way the stain result is).
  useEffect(() => {
    if (!mm || !sel) return undefined;
    let stale = false;
    setMmBusy(true); setMmErr("");
    const t = setTimeout(async () => {
      try {
        const r = await api("/technical/polygonize/base", { method: "POST", body: { token: mm.token, tags: [sel], ...params, code: cc.toUpperCase() } });
        if (stale) return;
        if (!r.features.features.length) throw new Error("Nothing of that tag falls inside this country.");
        const file = new File([JSON.stringify(r.features)], "mapmaking.geojson", { type: "application/geo+json" });
        const next = await stageRef.current({ file });
        if (stale) { discardStaged(next?.token); return; }
        changeRef.current(next);
      } catch (e) { if (!stale) setMmErr(errorText(e)); } finally { if (!stale) setMmBusy(false); }
    }, 400);
    return () => { stale = true; clearTimeout(t); };
  }, [mm, sel, params, cc]);

  // Look for the map (and read its colours) whenever the picture or the hand-drawn box changes.
  useEffect(() => {
    if (!up) return undefined;
    if (up.map?.found === false && !crop) { setRes(null); setBusy(false); return undefined; }
    let stale = false;
    setBusy(true); setErr("");
    const t = setTimeout(() => {
      api("/technical/stain/analyze", { method: "POST", body: { token: up.token, country: cc, crop } })
        .then(async (r) => {
          if (stale) return;
          setRes(r);
          const keep = (r.geojson?.features ?? []).filter((f) => !f.properties?.base);
          if (!keep.length) throw new Error("No coloured areas were found in that picture.");
          const file = new File([JSON.stringify({ type: "FeatureCollection", features: withShades(keep) })], "regions.geojson", { type: "application/geo+json" });
          const next = await stageRef.current({ file });
          if (stale) { discardStaged(next?.token); return; }
          discardStaged(staged.current);
          staged.current = next?.token;
          onChange(next);
        })
        .catch((e) => { if (!stale) { setErr(errorText(e)); setRes(null); } })
        .finally(() => { if (!stale) setBusy(false); });
    }, 250);
    return () => { stale = true; clearTimeout(t); };
  }, [up, crop, cc]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── drawing the box ──
  const frac = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    return [clamp((e.clientX - r.left) / r.width), clamp((e.clientY - r.top) / r.height)];
  };
  const onDown = (e) => {
    if (e.button) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    start.current = frac(e);
    drag.current = [...start.current, ...start.current];
    setDraft(drag.current);
  };
  const onMove = (e) => {
    if (!start.current) return;
    const [x, y] = frac(e);
    const [sx, sy] = start.current;
    drag.current = [Math.min(x, sx), Math.min(y, sy), Math.max(x, sx), Math.max(y, sy)];
    setDraft(drag.current);
  };
  const onUp = () => {
    const d = drag.current;
    start.current = null; drag.current = null; setDraft(null);
    if (d && d[2] - d[0] >= MIN_BOX && d[3] - d[1] >= MIN_BOX) setCrop(d);
  };

  const stop = () => {
    if (shot.current?.url?.startsWith("blob:")) URL.revokeObjectURL(shot.current.url);
    discardStaged(shot.current?.token);
    shot.current = null;
    setUp(null); setRes(null); setCrop(null); setDraft(null); setErr(""); setAdjust(false);
  };

  const needBox = up?.map?.found === false || !!err || (!!res && res.fit.confidence < 0.35);
  const showShot = !!up?.url && (needBox || adjust);

  return <div className="ar">
    <FilePicker kind="geometry" label="Region" hint={HINT} accept={ACCEPT} value={value} current={current}
      onChange={onChange} stage={stageArea} onImage={takePicture} cc={cc} />
    {mm && <div className="ar-stain">
      <div className="clue-chips" role="group" aria-label="Tag" style={{ maxHeight: 150, overflowY: "auto" }}>
        {mm.tags.map((t) => <button key={t.name} type="button" className={`fchip ${sel === t.name ? "on" : ""}`} aria-pressed={sel === t.name}
          onClick={() => setSel(t.name)}>{t.name}<span className="count">{t.count.toLocaleString()}</span></button>)}
      </div>
      <details>
        <summary style={{ cursor: "pointer", fontSize: 13 }}>Customize</summary>
        <ShapeControls params={params} onChange={setParams} />
      </details>
      <div className="ar-row">
        <span className="muted">
          {mmBusy ? "Drawing…" : mmErr ? mmErr
            : sel ? `Region set from “${sel}”`
              : `${mm.name} · ${(mm.total - mm.outside).toLocaleString()} locations in this country${mm.outside ? ` (${mm.outside.toLocaleString()} outside ignored)` : ""}. Pick a tag.`}
        </span>
        <button type="button" className="btn ghost" onClick={() => { setMm(null); setSel(""); setMmErr(""); }}>Close</button>
      </div>
    </div>}
    {up && <div className="ar-stain">
      {showShot && <div className="ar-shot" onPointerDown={onDown} onPointerMove={onMove} onPointerUp={onUp} onPointerCancel={onUp}>
        <img src={up.url} alt="The picture the areas are read from" draggable={false} />
        {rect(draft ?? crop) && <div className="ar-box" style={rect(draft ?? crop)} />}
      </div>}
      <div className="ar-row">
        <span className="muted">
          {busy ? "Looking for the map…"
            : err ? err
              : res ? `${(res.geojson?.features ?? []).filter((f) => !f.properties?.base).length} areas found${showShot ? ". Drag a box around the map to search again." : ""}`
                : "The map wasn't found: drag a box around it."}
        </span>
        {res && !needBox && <button type="button" className="btn ghost" onClick={() => setAdjust((a) => !a)}>{adjust ? "Hide picture" : "Adjust"}</button>}
        <button type="button" className="btn ghost" onClick={stop}>Close</button>
      </div>
    </div>}
  </div>;
}
