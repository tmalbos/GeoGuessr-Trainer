import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../../lib/api.js";
import { DEFAULT_DRAW_LEVEL_SHORTCUTS, sameShortcut } from "../../../lib/shortcut.js";
import { bboxOf, shadeFactor, shapes } from "./geoDraw.js";
import { discardStaged } from "./imageTools.js";
import { markKnown, newGid, sendItems, wire } from "./drawItems.js";
import PaintedLayer, { Units } from "./PaintedLayer.jsx";
import { setDrawBusy } from "./drawBusy.js";
import { TileBackdrop, TilesIcon, makeProjection } from "./drawTiles.jsx";
import "./draw.css";

const W = 1000;
const MAX_ZOOM = 80;
const DRAG_PX = 4;
const SHADES = 4; // keep equal to MAX_SHADE + 1 in src/api/routers/clues.py
const DEFAULT_COLOR = "#c8283a";

const ico = { width: 19, height: 19, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const TOOLS = [
  ["fill", "Fill a subdivision", <svg key="f" {...ico}><path d="M5 11l7-7 8 8-7 7z" /><path d="M5 11l8 8" /><path d="M20 17c0 1.5-1 2.5-1 2.5s-1-1-1-2.5a1 1 0 0 1 2 0z" /></svg>],
  ["paint", "Freeform paint", <svg key="p" {...ico}><path d="M18 3l3 3-9 9-3-3z" /><path d="M9 12c-3 0-4 2-4 4s-1 3-2 4c3 1 8 0 8-5" /></svg>],
  ["erase", "Eraser (hold Shift and click to erase a whole subdivision)", <svg key="e" {...ico}><path d="M4 16l9-9 6 6-6 6H8z" /><path d="M8 20h12" /></svg>],
];

// The view may leave the country: it can be panned up to 3 whole maps past each edge.
const clampView = (v, H) => ({ w: v.w, x: Math.min(Math.max(v.x, -3 * W), 4 * W - v.w), y: Math.min(Math.max(v.y, -3 * H), 4 * H - (v.w * H) / W) });
const unitClamp = (v) => Math.min(1, Math.max(0, v)); // a locked view is stored as fractions of the map (0..1)
const zoomView = (v, px, py, f, H) => {
  const w = Math.min(W, Math.max(W / MAX_ZOOM, v.w * f));
  const k = w / v.w;
  return clampView({ w, x: px - (px - v.x) * k, y: py - (py - v.y) * k }, H);
};
const pathOf = (pts) => `M${(pts.length === 1 ? [pts[0], pts[0]] : pts).map((p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`).join("L")}`;
const alpha = (shade) => 0.85 * shadeFactor(shade);

/** Draw a regional clue's area on top of its country, in fixed shades of the stain colour (Settings).
 *  Shade 1 is the main tone, the next ones are lighter (= rarer). Fill = click a subdivision, paint = freeform strokes,
 *  Shift + wheel changes the shade. Nothing is named and nothing is clickable on the card, so there is no renaming or images.
 *  The drawing is built by the same endpoint as DrawMap, turned into a GeoJSON with a `shade` per feature, staged through
 *  `stage` and handed to `onResult` (null when nothing is drawn). */
export default function ShadeDraw({ cc, stage, onResult, stored, storedView, onView }) {
  const [base, setBase] = useState(null);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [nLevels, setNLevels] = useState(0);
  const [levelKeys, setLevelKeys] = useState(DEFAULT_DRAW_LEVEL_SHORTCUTS);
  const [units, setUnits] = useState(null);
  const [tool, setTool] = useState("fill");
  const [size, setSize] = useState(40);
  const [items, setItems] = useState([]);
  const [live, setLive] = useState(null);
  const [sel, setSel] = useState(0);
  const [color, setColor] = useState(DEFAULT_COLOR);
  const [view, setView] = useState({ x: 0, y: 0, w: W });
  const [busy, setBusy] = useState(false);
  const [tiles, setTiles] = useState(false); // show the real map behind the drawing
  const [locked, setLocked] = useState(storedView ?? null);
  const viewApplied = useRef(false);
  const known = useRef(new Set()); // gids of the shapes the server already holds: their geometry is not sent again
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
    api("/settings").then((s) => {
      if (off) return;
      setColor(s.stain_color || DEFAULT_COLOR);
      if (Array.isArray(s.draw_level_shortcuts)) setLevelKeys(s.draw_level_shortcuts);
    }).catch(() => {});
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
      if (d.levels && level === 0) setLevel(1);
      setUnits(d.units);
    }).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc, level]);

  // Editing a saved region (made with this tool or not): its polygons come back as shapes, one per shade,
  // so they can be repainted or erased.
  useEffect(() => {
    if (!stored) return undefined;
    let off = false;
    fetch(`/api/clues/${cc}/geo/${stored}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((g) => {
        if (off) return;
        const byShade = new Map();
        g.geometries.forEach((geom, i) => {
          const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
          if (!polys.length) return;
          const s = Math.min(SHADES - 1, g.shades?.[i] ?? 0);
          byShade.set(s, [...(byShade.get(s) ?? []), ...polys]);
        });
        setItems([...byShade].sort((a, b) => a[0] - b[0])
          .map(([slot, coordinates]) => ({ op: "shape", slot, gid: newGid(), geometry: { type: "MultiPolygon", coordinates } })));
      })
      .catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc, stored]);

  useEffect(() => {
    if (!items.length) {
      setBusy(false);
      if (had.current) { had.current = false; resultRef.current(null); }
      return undefined;
    }
    had.current = true;
    let stale = false;
    const ac = new AbortController();
    setBusy(true); setErr("");
    const t = setTimeout(async () => {
      try {
        // One "category" per shade (named s0..s4); the server keeps them disjoint, a later item wins.
        const sent = () => items.map((it) => ({ ...wire(it, known.current), name: it.op === "erase" ? "" : `s${it.slot}` }));
        const fc = await sendItems(known.current, `/clues/${cc}/draw/build`, () => ({ items: sent(), colored: false }), { signal: ac.signal });
        if (stale) return;
        markKnown(items, known.current);
        const features = fc.features.map((f) => ({
          type: "Feature",
          properties: { shade: Number(f.properties.name.slice(1)), drawn: true },
          geometry: f.geometry,
        }));
        if (!features.length) { resultRef.current(null); return; }
        const file = new File([JSON.stringify({ type: "FeatureCollection", features })], "shades.geojson", { type: "application/geo+json" });
        const up = await stageRef.current({ file });
        if (stale) { discardStaged(up?.token); return; }
        resultRef.current(up);
      } catch (e) { if (!stale) setErr(errorText(e)); } finally { if (!stale) setBusy(false); }
    }, 600);
    return () => { stale = true; clearTimeout(t); ac.abort(); };
  }, [items, cc]);
  useEffect(() => { setDrawBusy(busy); }, [busy]);
  useEffect(() => () => setDrawBusy(false), []);

  const proj = useMemo(() => { const bb = base && bboxOf(base); return bb ? makeProjection(bb) : null; }, [base]);
  const landD = useMemo(() => (proj ? shapes(base, proj.fwd).polys : ""), [proj, base]);
  const unitPaths = useMemo(() => (proj && units ? units.map((u) => ({ id: u.id, name: u.name, d: shapes(u.geometry, proj.fwd).polys })) : []), [proj, units]);
  // Shades are drawn opaque (the colour mixed into the land colour), so overlapping strokes look the same as the server result.
  const fillOf = useMemo(() => (s) => `color-mix(in srgb, ${color} ${Math.round(alpha(s) * 100)}%, #f6f4e4)`, [color]);
  const pickRef = useRef(null);
  // Bucket fill: the server works out the connected empty area (or the clicked colour's area) inside the division under the click.
  pickRef.current = async (u, e) => {
    const p = toSvg(e);
    if (!p) return;
    const whole = e.shiftKey; // Shift: fill every piece of the division, reachable or not
    const slot = sel;
    try {
      const sent = () => items.map((it) => ({ ...wire(it, known.current), name: it.op === "erase" ? "" : `s${it.slot}` }));
      const r = await sendItems(known.current, `/clues/${cc}/draw/fill`, () => ({ items: sent(), level, id: u.id, point: proj.inv(p), whole }));
      markKnown(items, known.current);
      known.current.add(r.gid);
      setItems((it) => [...it, { op: "shape", slot, gid: r.gid, geometry: r.geometry }]);
    } catch (ex) { setErr(errorText(ex)); }
  };
  const onPick = useMemo(() => (u, e) => pickRef.current(u, e), []);

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
          setSel((s) => (s + (d > 0 ? -1 : 1) + SHADES) % SHADES);
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

  useEffect(() => {
    const onKey = (e) => {
      const t = e.target;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable)) return;
      const at = levelKeys.findIndex((k) => sameShortcut(e, k)) + 1;
      if (!at || at > nLevels) return;
      e.preventDefault();
      setLevel(at);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [levelKeys, nLevels]);

  // A saved clue opens framed the way it was locked.
  useEffect(() => {
    if (!proj || !locked || viewApplied.current) return;
    viewApplied.current = true;
    const w = W / locked.k;
    setView(clampView({ w, x: locked.fx * W - w / 2, y: locked.fy * proj.H - (w * proj.H) / W / 2 }, proj.H));
  }, [proj]); // eslint-disable-line react-hooks/exhaustive-deps

  // Lock = keep the zoom and position on screen right now: the card then opens on that framing.
  const toggleLock = () => {
    if (locked) { setLocked(null); onView?.(null); return; }
    const next = {
      k: Math.round((W / view.w) * 1000) / 1000,
      fx: unitClamp(Math.round(((view.x + view.w / 2) / W) * 10000) / 10000),
      fy: unitClamp(Math.round(((view.y + (view.w * proj.H) / W / 2) / proj.H) * 10000) / 10000),
    };
    setLocked(next);
    onView?.(next);
  };
  const lockBox = locked && proj
    ? clampView({ w: W / locked.k, x: locked.fx * W - W / locked.k / 2, y: locked.fy * proj.H - proj.H / locked.k / 2 }, proj.H)
    : null;

  const painting = tool !== "fill";

  const toSvg = (e) => {
    const m = svg.current.getScreenCTM();
    if (!m) return null;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
    return [p.x, p.y];
  };

  // Shift + eraser on a subdivision works like the fill bucket: it removes only the connected painted part under the click.
  const eraseIn = async (id, p) => {
    try {
      const sent = () => items.map((it) => ({ ...wire(it, known.current), name: it.op === "erase" ? "" : `s${it.slot}` }));
      const r = await sendItems(known.current, `/clues/${cc}/draw/fill`, () => ({ items: sent(), level, id, point: proj.inv(p), erase: true }));
      markKnown(items, known.current);
      if (r.geometry) {
        known.current.add(r.gid);
        setItems((it) => [...it, { op: "erase", name: "", level, id, gid: r.gid, geometry: r.geometry }]);
      }
    } catch (ex) { setErr(errorText(ex)); }
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
      if (hit) eraseIn(Number(hit.dataset.id), p);
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
    if (!p || Math.hypot(p[0] - lastPt[0], p[1] - lastPt[1]) < (2 * view.w) / W) return;
    d.pts.push(p);
    setLive([...d.pts]);
  };
  const up = () => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.kind !== "draw") return;
    setLive(null);
    // The brush is a screen size: zoomed in, the same brush covers less of the map.
    const item = { op: tool === "erase" ? "erase" : "stroke", width: ((size * view.w) / W / proj.s) * Math.cos((proj.inv(d.pts[0])[1] * Math.PI) / 180), points: d.pts.map(proj.inv), w: (size * view.w) / W, d: pathOf(d.pts) };
    setItems((it) => [...it, tool === "erase" ? item : { ...item, slot: sel }]);
  };

  const unit = (view.w / W) * size;

  return <>
    <div className={`dm ${tiles ? "tiles" : ""}`}>
      {proj ? <>
        {tiles && <TileBackdrop proj={proj} view={view} />}
        <div className="dm-levels" role="group" aria-label="Subdivision level">
          {(nLevels ? Array.from({ length: nLevels }, (_, i) => [i + 1, `Adm ${i + 1}`]) : [[0, "National"]]).map(([v, l]) =>
            <button key={l} type="button" className={level === v ? "on" : ""} aria-pressed={level === v} onClick={() => setLevel(v)}>{l}</button>)}
        </div>
        <svg ref={svg} className="dm-svg" viewBox={`${view.x} ${view.y} ${view.w} ${(view.w * proj.H) / W}`} role="img" aria-label="Map to draw on"
          style={{ cursor: painting ? "crosshair" : undefined }} onContextMenu={(e) => e.preventDefault()}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <path className="cl-r" d={landD} fillRule="evenodd" />
          <Units paths={unitPaths} click={tool === "fill"} onPick={onPick} />
          <g className="dm-paint"><PaintedLayer items={items} fwd={proj.fwd} H={proj.H} land={landD} fillOf={fillOf} /></g>
          {lockBox && <rect x={lockBox.x} y={lockBox.y} width={lockBox.w} height={(lockBox.w * proj.H) / W} fill="none" stroke="#ffd04d"
            strokeWidth="2" strokeDasharray="8 5" vectorEffect="non-scaling-stroke" style={{ pointerEvents: "none" }} />}
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
      <button type="button" className="ed-tool" aria-pressed={!!locked} aria-label={locked ? "Unlock view" : "Lock view"} disabled={!proj}
        title={locked ? "Unlock view (the card shows the whole map)" : "Lock view: the card opens on the zoom and position on screen now"} onClick={toggleLock}>
        <svg {...ico}><rect x="5" y="11" width="14" height="9" rx="2" /><path d={locked ? "M8 11V8a4 4 0 0 1 8 0v3" : "M8 11V8a4 4 0 0 1 7.5-2"} /></svg></button>
      <button type="button" className="ed-tool" aria-pressed={tiles} aria-label="Show the map behind the drawing" disabled={!proj}
        title="Show the map behind the drawing" onClick={() => setTiles((v) => !v)}><TilesIcon /></button>
      <span className={`dm-status ${err && proj ? "err" : "muted"}`}>{proj && err ? err : busy ? "Saving…" : ""}</span>
    </div>
    {proj && <div className="dm-cats">
      <div className="clue-chips" role="group" aria-label="Shade">
        {Array.from({ length: SHADES }, (_, s) => <button key={s} type="button" className={`fchip ${s === sel ? "on" : ""}`} aria-pressed={s === sel}
          onClick={() => setSel(s)}><i className="dm-sw" style={{ background: color, opacity: alpha(s) }} />Shade {s + 1}</button>)}
      </div>
      <span className="muted">Shift + scroll to change shade. Lighter = rarer.</span>
    </div>}
  </>;
}
