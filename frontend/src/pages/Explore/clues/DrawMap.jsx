import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../../lib/api.js";
import { bboxOf, shapes } from "./geoDraw.js";
import { discardStaged } from "./imageTools.js";
import "./draw.css";

const W = 1000;
const PAD = 6;

const ico = { width: 20, height: 20, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const TOOLS = [
  ["fill", "Fill the subdivision you click", <svg key="f" {...ico}><path d="M5 11l7-7 8 8-7 7z" /><path d="M5 11l8 8" /><path d="M20 17c0 1.5-1 2.5-1 2.5s-1-1-1-2.5a1 1 0 0 1 2 0z" /></svg>],
  ["paint", "Freeform paint", <svg key="p" {...ico}><path d="M18 3l3 3-9 9-3-3z" /><path d="M9 12c-3 0-4 2-4 4s-1 3-2 4c3 1 8 0 8-5" /></svg>],
  ["erase", "Eraser", <svg key="e" {...ico}><path d="M4 16l9-9 6 6-6 6H8z" /><path d="M8 20h12" /></svg>],
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

/** Draw a regional clue as colored categories on its country. Fill = the subdivision you click (at the level picked on the right);
 *  paint = freeform strokes clipped to the outline. Every category name gets its own color (the server keeps neighbours apart).
 *  The server builds the polygons from the list of actions; the result is staged through `stage` and handed to `onResult`
 *  (null when nothing is drawn). */
export default function DrawMap({ cc, stage, onResult }) {
  const [base, setBase] = useState(null);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [nLevels, setNLevels] = useState(0);
  const [units, setUnits] = useState(null);
  const [tool, setTool] = useState("fill");
  const [name, setName] = useState("");
  const [size, setSize] = useState(24);
  const [items, setItems] = useState([]);
  const [shown, setShown] = useState([]);
  const [live, setLive] = useState(null);
  const [busy, setBusy] = useState(false);
  const svg = useRef(null);
  const drag = useRef(null);
  const had = useRef(false);
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const resultRef = useRef(onResult);
  resultRef.current = onResult;

  useEffect(() => {
    let off = false;
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
    setBusy(true);
    const t = setTimeout(async () => {
      try {
        const fc = await api(`/clues/${cc}/draw/build`, { method: "POST", body: { items } });
        if (stale) return;
        setShown(fc.features);
        if (!fc.features.length) { resultRef.current(null); return; }
        const file = new File([JSON.stringify(fc)], "drawing.geojson", { type: "application/geo+json" });
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
  const shownPaths = useMemo(() => (proj ? shown.map((f) => ({ name: f.properties.name, color: f.properties.color, d: shapes(f.geometry, proj.fwd).polys })) : []), [proj, shown]);

  if (!proj) return err ? <p className="err">{err}</p> : <p className="muted">Loading map…</p>;

  const painting = tool !== "fill";
  const names = [...new Set(items.map((i) => i.name).filter(Boolean))];

  const toSvg = (e) => {
    const m = svg.current.getScreenCTM();
    if (!m) return null;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
    return [p.x, p.y];
  };
  const down = (e) => {
    if (!painting || e.button) return;
    if (tool === "paint" && !name.trim()) { setErr("Type a category name first."); return; }
    const p = toSvg(e);
    if (!p) return;
    e.currentTarget.setPointerCapture(e.pointerId);
    drag.current = [p];
    setLive([p]);
  };
  const move = (e) => {
    const d = drag.current;
    if (!d) return;
    const p = toSvg(e);
    const last = d[d.length - 1];
    if (!p || Math.hypot(p[0] - last[0], p[1] - last[1]) < 2) return;
    d.push(p);
    setLive([...d]);
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    setLive(null);
    if (!d) return;
    const item = { op: tool === "erase" ? "erase" : "stroke", name: tool === "erase" ? "" : name.trim(), width: size / proj.s, points: d.map(proj.inv) };
    setItems((it) => [...it, item]);
  };
  const fill = (u) => {
    if (!name.trim()) { setErr("Type a category name first."); return; }
    setItems((it) => [...it, { op: "fill", name: name.trim(), level, id: u.id }]);
  };

  const livePts = live && (live.length === 1 ? [live[0], live[0]] : live);

  return <div className="dm">
    <svg ref={svg} className="dm-svg" viewBox={`0 0 ${W} ${proj.H}`} role="img" aria-label="Map to draw on"
      style={{ cursor: painting ? "crosshair" : undefined }}
      onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
      <path className="cl-r" d={landD} fillRule="evenodd" />
      {shownPaths.map((p) => <path key={p.name} className="dm-res" d={p.d} fillRule="evenodd" style={{ fill: p.color }} />)}
      {unitPaths.map((u) => <path key={u.id} className={`dm-unit ${tool === "fill" ? "click" : ""}`} d={u.d} fillRule="evenodd"
        style={tool === "fill" ? undefined : { pointerEvents: "none" }} onClick={tool === "fill" ? () => fill(u) : undefined}><title>{u.name}</title></path>)}
      {livePts && <path d={`M${livePts.map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("L")}`} fill="none" strokeLinecap="round" strokeLinejoin="round"
        strokeWidth={size} stroke={tool === "erase" ? "#ff7357" : "#ffffff"} strokeOpacity=".5" style={{ pointerEvents: "none" }} />}
    </svg>

    <div className="dm-side">
      <div className="tabs" role="group" aria-label="Subdivision level">
        {["National", ...Array.from({ length: nLevels }, (_, i) => `Adm ${i + 1}`)].map((l, i) =>
          <button key={l} type="button" className={level === i ? "on" : ""} aria-pressed={level === i} onClick={() => setLevel(i)}>{l}</button>)}
      </div>
      <input type="text" list={`dm-names-${cc}`} value={name} aria-label="Category name" placeholder="Category name"
        onChange={(e) => { setName(e.target.value); setErr(""); }} />
      <datalist id={`dm-names-${cc}`}>{names.map((n) => <option key={n} value={n} />)}</datalist>
      {painting && <input type="range" min="4" max="80" value={size} aria-label="Brush size" title="Brush size" onChange={(e) => setSize(Number(e.target.value))} />}
      <div className="dm-tools" role="group" aria-label="Tool">
        {TOOLS.map(([k, label, icon]) => <button key={k} type="button" className="dm-tool" aria-pressed={tool === k} aria-label={label} title={label}
          onClick={() => setTool(k)}>{icon}</button>)}
        <button type="button" className="dm-tool" aria-label="Undo" title="Undo" disabled={!items.length} onClick={() => setItems((it) => it.slice(0, -1))}>↶</button>
        <button type="button" className="dm-tool" aria-label="Clear" title="Clear all" disabled={!items.length} onClick={() => setItems([])}>×</button>
      </div>
    </div>

    <div className="dm-note">
      {shown.length > 0 && <div className="clue-chips">{shown.map((f) =>
        <button key={f.properties.name} type="button" className="fchip" onClick={() => setName(f.properties.name)}>
          <i className="dm-sw" style={{ background: f.properties.color }} /> {f.properties.name}</button>)}</div>}
      {err ? <p className="err">{err}</p> : busy ? <p className="muted">Saving…</p> : null}
    </div>
  </div>;
}
