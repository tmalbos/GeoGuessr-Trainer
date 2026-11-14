import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../../lib/api.js";
import { bboxOf, shadeFactor, shapes } from "./geoDraw.js";
import { discardStaged } from "./imageTools.js";
import "./draw.css";

const W = 1000;
const PAD = 6;
const MAX_ZOOM = 80;
const DRAG_PX = 4;
const SHADES = 5; // keep equal to MAX_SHADE + 1 in src/api/routers/clues.py
const TOP = SHADES - 1;
const DEFAULT_COLOR = "#c8283a";

const ico = { width: 19, height: 19, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const TOOLS = [
  ["fill", "Fill a subdivision", <svg key="f" {...ico}><path d="M5 11l7-7 8 8-7 7z" /><path d="M5 11l8 8" /><path d="M20 17c0 1.5-1 2.5-1 2.5s-1-1-1-2.5a1 1 0 0 1 2 0z" /></svg>],
  ["paint", "Freeform paint", <svg key="p" {...ico}><path d="M18 3l3 3-9 9-3-3z" /><path d="M9 12c-3 0-4 2-4 4s-1 3-2 4c3 1 8 0 8-5" /></svg>],
  ["erase", "Eraser (hold Shift and click to erase a whole subdivision)", <svg key="e" {...ico}><path d="M4 16l9-9 6 6-6 6H8z" /><path d="M8 20h12" /></svg>],
];

function makeProjection(bb) {
  const [x0, y0, x1, y1] = bb;
  const k = Math.cos((((y0 + y1) / 2) * Math.PI) / 180) || 1;
  const w = Math.max((x1 - x0) * k, 1e-6);
  const h = Math.max(y1 - y0, 1e-6);
  const H = Math.round(Math.min(1400, Math.max(200, (W * h) / w)));
  const s = Math.min((W - 2 * PAD) / w, (H - 2 * PAD) / h);
  const ox = (W - w * s) / 2;
  const oy = (H - h * s) / 2;
  return {
    H, s,
    fwd: ([lon, lat]) => [ox + (lon - x0) * k * s, oy + (y1 - lat) * s],
    inv: ([x, y]) => [x0 + (x - ox) / (k * s), y1 - (y - oy) / s],
  };
}

const clampView = (v, H) => ({ w: v.w, x: Math.min(Math.max(v.x, 0), W - v.w), y: Math.min(Math.max(v.y, 0), H - (v.w * H) / W) });
const zoomView = (v, px, py, f, H) => {
  const w = Math.min(W, Math.max(W / MAX_ZOOM, v.w * f));
  const k = w / v.w;
  return clampView({ w, x: px - (px - v.x) * k, y: py - (py - v.y) * k }, H);
};
const pathOf = (pts) => `M${(pts.length === 1 ? [pts[0], pts[0]] : pts).map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("L")}`;
const alpha = (shade, top) => 0.85 * shadeFactor(shade, top);

/** Draw a regional clue's area on top of its country, in fixed shades of the stain colour (Settings).
 *  Shade 1 is the main tone, the next ones are lighter (= rarer). Fill = click a subdivision, paint = freeform strokes,
 *  Shift + wheel changes the shade. Nothing is named and nothing is clickable on the card, so there is no renaming or images.
 *  The drawing is built by the same endpoint as DrawMap, turned into a GeoJSON with a `shade` per feature, staged through
 *  `stage` and handed to `onResult` (null when nothing is drawn). */
export default function ShadeDraw({ cc, stage, onResult }) {
  const [base, setBase] = useState(null);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [nLevels, setNLevels] = useState(0);
  const [units, setUnits] = useState(null);
  const [tool, setTool] = useState("fill");
  const [size, setSize] = useState(24);
  const [items, setItems] = useState([]);
  const [shown, setShown] = useState([]);
  const [live, setLive] = useState(null);
  const [sel, setSel] = useState(0);
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [view, setView] = useState({ x: 0, y: 0, w: W });
  const [busy, setBusy] = useState(false);
  const svg = useRef(null);
  const drag = useRef(null);
  const had = useRef(false);
  const shiftAt = useRef(0);
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const resultRef = useRef(onResult);
  resultRef.current = onResult;

  useEffect(() => {
    let off = false;
    api("/settings").then((s) => !off && setColor(s.stain_color || DEFAULT_COLOR)).catch(() => {});
    api(`/clues/${cc}/base`).then((b) => {
      if (off) return;
      if (b.outline) setBase(b.outline); else setErr("This country has no local map data.");
    }).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc]);

  useEffect(() => {
    let off = false;
    setUnits(null);
    api(`/clues/${cc}/draw/units?level=${level}`).then((d) => {
      if (off) return;
      setNLevels(d.levels);
      setUnits(d.units);
    }).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc, level]);

  useEffect(() => {
    if (!items.length) {
      if (had.current) { had.current = false; setShown([]); resultRef.current(null); }
      return undefined;
    }
    had.current = true;
    let stale = false;
    setBusy(true); setErr("");
    const t = setTimeout(async () => {
      try {
        // One "category" per shade (named s0..s4); the server keeps them disjoint, a later item wins.
        const sent = items.map((i) => ({ ...i, name: i.op === "erase" ? "" : `s${i.slot}` }));
        const fc = await api(`/clues/${cc}/draw/build`, { method: "POST", body: { items: sent, colored: false } });
        if (stale) return;
        const features = fc.features.map((f) => ({
          type: "Feature",
          properties: { shade: Number(f.properties.name.slice(1)) },
          geometry: f.geometry,
        }));
        setShown(features);
        if (!features.length) { resultRef.current(null); return; }
        const file = new File([JSON.stringify({ type: "FeatureCollection", features })], "shades.geojson", { type: "application/geo+json" });
        const up = await stageRef.current({ file });
        if (stale) { discardStaged(up?.token); return; }
        resultRef.current(up);
      } catch (e) { if (!stale) setErr(errorText(e)); } finally { if (!stale) setBusy(false); }
    }, 150);
    return () => { stale = true; clearTimeout(t); };
  }, [items, cc]);

  const proj = useMemo(() => { const bb = base && bboxOf(base); return bb ? makeProjection(bb) : null; }, [base]);
  const landD = useMemo(() => (proj ? shapes(base, proj.fwd).polys : ""), [proj, base]);
  const unitPaths = useMemo(() => (proj && units ? units.map((u) => ({ id: u.id, name: u.name, d: shapes(u.geometry, proj.fwd).polys })) : []), [proj, units]);
  const shownPaths = useMemo(() => (proj ? shown.map((f, i) => ({ key: i, shade: f.properties.shade, d: shapes(f.geometry, proj.fwd).polys })) : []), [proj, shown]);
  const top = Math.max(0, ...shown.map((f) => f.properties.shade)); // same rule the card uses

  // Wheel zooms around the cursor; Shift + wheel cycles the shade.
  useEffect(() => {
    const el = svg.current;
    if (!el || !proj) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      if (e.shiftKey) {
        const d = e.deltaY || e.deltaX;
        const now = performance.now();
        if (d && now - shiftAt.current > 120) {
          shiftAt.current = now;
          setSel((s) => (s + (d > 0 ? 1 : -1) + SHADES) % SHADES);
        }
        return;
      }
      const m = el.getScreenCTM();
      if (!m) return;
      const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
      const dy = e.deltaY * (e.deltaMode === 1 ? 16 : 1);
      setView((v) => zoomView(v, p.x, p.y, Math.exp(dy * 0.0015), proj.H));
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [proj]);

  const painting = tool !== "fill";

  const toSvg = (e) => {
    const m = svg.current.getScreenCTM();
    if (!m) return null;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
    return [p.x, p.y];
  };

  const down = (e) => {
    const alt = e.button === 1 || e.button === 2;
    if (alt || (tool === "fill" && e.button === 0)) {
      if (alt) { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); }
      drag.current = { kind: "pan", x: e.clientX, y: e.clientY, v: view, moved: alt };
      return;
    }
    if (e.button) return;
    const p = toSvg(e);
    if (!p) return;
    if (tool === "erase" && e.shiftKey) {
      const pt = new DOMPoint(p[0], p[1]);
      const hit = [...svg.current.querySelectorAll(".dm-unit")].find((u) => u.isPointInFill(pt));
      if (hit) setItems((it) => [...it, { op: "erase", name: "", level, id: Number(hit.dataset.id) }]);
      return;
    }
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = { kind: "draw", pts: [p] };
    setLive([p]);
  };
  const move = (e) => {
    const d = drag.current;
    if (!d) return;
    if (d.kind === "pan") {
      const dx = e.clientX - d.x;
      const dy = e.clientY - d.y;
      if (!d.moved) {
        if (Math.hypot(dx, dy) < DRAG_PX) return;
        d.moved = true;
        e.currentTarget.setPointerCapture(e.pointerId);
      }
      const k = d.v.w / svg.current.getBoundingClientRect().width;
      setView(clampView({ ...d.v, x: d.v.x - dx * k, y: d.v.y - dy * k }, proj.H));
      return;
    }
    const p = toSvg(e);
    const lastPt = d.pts[d.pts.length - 1];
    if (!p || Math.hypot(p[0] - lastPt[0], p[1] - lastPt[1]) < 2) return;
    d.pts.push(p);
    setLive([...d.pts]);
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.kind !== "draw") return;
    setLive(null);
    // The brush is a screen size: zoomed in, the same brush covers less of the map.
    const item = { op: tool === "erase" ? "erase" : "stroke", width: (size * view.w) / W / proj.s, points: d.pts.map(proj.inv) };
    setItems((it) => [...it, tool === "erase" ? item : { ...item, slot: sel }]);
  };

  const unit = (view.w / W) * size;

  return <>
    <div className="dm">
      {proj ? <>
        <div className="dm-levels" role="group" aria-label="Subdivision level">
          {["National", ...Array.from({ length: nLevels }, (_, i) => `Adm ${i + 1}`)].map((l, i) =>
            <button key={l} type="button" className={level === i ? "on" : ""} aria-pressed={level === i} onClick={() => setLevel(i)}>{l}</button>)}
        </div>
        <svg ref={svg} className="dm-svg" viewBox={`${view.x} ${view.y} ${view.w} ${(view.w * proj.H) / W}`} role="img" aria-label="Map to draw on"
          style={{ cursor: painting ? "crosshair" : undefined }} onContextMenu={(e) => e.preventDefault()}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <path className="cl-r" d={landD} fillRule="evenodd" />
          {unitPaths.map((u) => <path key={u.id} data-id={u.id} className={`dm-unit ${tool === "fill" ? "click" : ""}`} d={u.d} fillRule="evenodd"
            style={tool === "fill" ? undefined : { pointerEvents: "none" }}
            onClick={tool === "fill" ? () => setItems((it) => [...it, { op: "fill", level, id: u.id, slot: sel }]) : undefined}><title>{u.name}</title></path>)}
          {shownPaths.map((p) => <path key={p.key} className="dm-res" d={p.d} fillRule="evenodd"
            style={{ fill: color, fillOpacity: alpha(p.shade, top), pointerEvents: "none" }} />)}
          {live && <path d={pathOf(live)} fill="none" strokeLinecap="round" strokeLinejoin="round" strokeWidth={unit}
            stroke={tool === "erase" ? "#ff7357" : "#ffffff"} strokeOpacity=".5" style={{ pointerEvents: "none" }} />}
        </svg>
      </> : err ? <p className="err">{err}</p> : <p className="muted">Loading map…</p>}
    </div>
    <div className="ed-tools dm-tools" role="toolbar" aria-label="Draw tools">
      {TOOLS.map(([k, label, icon]) => <button key={k} type="button" className="ed-tool" aria-pressed={tool === k} aria-label={label} title={label}
        onClick={() => setTool(k)}>{icon}</button>)}
      {painting && <input type="range" min="4" max="80" value={size} aria-label="Brush size" title="Brush size" onChange={(e) => setSize(Number(e.target.value))} />}
      <i className="ed-sep" />
      <button type="button" className="ed-tool" aria-label="Undo" title="Undo" disabled={!items.length} onClick={() => setItems((it) => it.slice(0, -1))}>↶</button>
      <button type="button" className="ed-tool" aria-label="Clear" title="Clear all" disabled={!items.length} onClick={() => setItems([])}>×</button>
      <span className={`dm-status ${err && proj ? "err" : "muted"}`}>{proj && err ? err : busy ? "Saving…" : ""}</span>
    </div>
    {proj && <div className="dm-cats">
      <div className="clue-chips" role="group" aria-label="Shade">
        {Array.from({ length: SHADES }, (_, s) => <button key={s} type="button" className={`fchip ${s === sel ? "on" : ""}`} aria-pressed={s === sel}
          onClick={() => setSel(s)}><i className="dm-sw" style={{ background: color, opacity: alpha(s, TOP) }} />Shade {s + 1}</button>)}
      </div>
      <span className="muted">Shift + scroll to change shade. Lighter = rarer.</span>
    </div>}
  </>;
}
