import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../../lib/api.js";
import { bboxOf, shapes } from "./geoDraw.js";
import { discardStaged, loadImage, stagedUrl } from "./imageTools.js";
import { AreaName, NAME_PX, areaNameSpot } from "./RegionPreview.jsx";
import ClueImage from "./ClueImage.jsx";
import "./draw.css";

const W = 1000;
const PAD = 6;
const MAX_ZOOM = 80;
const DRAG_PX = 4;

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

const MAX_PX = 640;

// Final pixels of an image slot -> a small data URI (the area's own "extra").
async function toDataUri(url) {
  const img = await loadImage(url);
  const k = Math.min(1, MAX_PX / Math.max(img.naturalWidth, img.naturalHeight));
  const c = document.createElement("canvas");
  c.width = Math.max(1, Math.round(img.naturalWidth * k));
  c.height = Math.max(1, Math.round(img.naturalHeight * k));
  c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
  return c.toDataURL("image/webp", 0.8);
}

// The same image slot a regional clue's photo uses, for one category. `value` = its stored data URI (or undefined).
function CategoryImage({ name, value, onChange }) {
  const ed = useRef(null);
  const col = useRef(null);
  const seq = useRef(0);
  const valueRef = useRef(value);
  valueRef.current = value;
  const [initial] = useState(value ?? null);
  const [gone, setGone] = useState(false);
  const [blur, setBlur] = useState(false);
  const [maxH, setMaxH] = useState(null);
  const onState = (has, sig) => {
    const n = ++seq.current;
    if (!has) { if (valueRef.current) onChange(name, null); return; }
    if (sig === ":0") return; // untouched: still the stored image
    ed.current.resolve().then((token) => token && toDataUri(stagedUrl(token)))
      .then((uri) => { if (uri && n === seq.current) onChange(name, uri); }).catch(() => {});
  };
  return <div className="dm-cat" ref={col}>
    <ClueImage ref={ed} current={gone ? null : initial} colRef={col} onState={onState} blur={blur} onBlur={setBlur} maxH={maxH} onMaxH={setMaxH}
      onRemove={() => { setGone(true); onChange(name, null); }} />
  </div>;
}

/** Draw a regional clue as colored categories on its country. Fill = the subdivision you click (level chosen on the map's top-left);
 *  paint = freeform strokes clipped to the outline. Everything you draw goes into the selected category (a color slot, named
 *  "Unnamed N" until renamed in the bar under the toolbar). Shift + wheel cycles through the used colors and one new one
 *  (kept only once something is painted with it). Wheel zooms; drag pans (Fill tool), middle / right button pans with any tool.
 *  The server builds the colored polygons from the list of actions; the result is staged through `stage` and handed to `onResult`
 *  (null when nothing is drawn). `trailing` goes at the end of the toolbar row (the Import | Draw switch). */
export default function DrawMap({ cc, stage, onResult, trailing, showNames }) {
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
  const [picked, setSel] = useState(0); // color slot the user picked (see `sel` below)
  const [custom, setCustom] = useState({}); // slot -> name given by the user
  const [palette, setPalette] = useState([]); // colors from the server, slot i uses palette[i % length]
  const [view, setView] = useState({ x: 0, y: 0, w: W });
  const [busy, setBusy] = useState(false);
  const [images, setImages] = useState({});
  const [colored, setColored] = useState(true);
  const wrap = useRef(null);
  const shiftAt = useRef(0);
  const cycle = useRef({ options: [0], sel: 0 });
  const svg = useRef(null);
  const drag = useRef(null);
  const had = useRef(false);
  const stageRef = useRef(stage);
  stageRef.current = stage;
  const resultRef = useRef(onResult);
  resultRef.current = onResult;
  const nameOf = (s) => custom[s]?.trim() || `Unnamed ${s + 1}`;
  const colorOf = (s) => (palette.length ? palette[s % palette.length] : "#8b98a8");

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
      setPalette(d.colors ?? []);
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
        const sent = items.map((i, k) => {
          const first = items.findIndex((x) => x.slot === i.slot && x.op !== "erase") === k;
          return { ...i, name: i.op === "erase" ? "" : nameOf(i.slot), ...(first && images[i.slot] ? { extra: images[i.slot] } : {}) };
        });
        const fc = await api(`/clues/${cc}/draw/build`, { method: "POST", body: { items: sent, colored } });
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
  }, [items, images, custom, cc, colored]);

  const proj = useMemo(() => { const bb = base && bboxOf(base); return bb ? makeProjection(bb) : null; }, [base]);
  const landD = useMemo(() => (proj ? shapes(base, proj.fwd).polys : ""), [proj, base]);
  const unitPaths = useMemo(() => (proj && units ? units.map((u) => ({ id: u.id, name: u.name, d: shapes(u.geometry, proj.fwd).polys })) : []), [proj, units]);
  const shownPaths = useMemo(() => (proj ? shown.map((f) => ({ name: f.properties.name, color: f.properties.color, d: shapes(f.geometry, proj.fwd).polys })) : []), [proj, shown]);
  // Names on the drawn areas (the "Show category names" setting): printed where they fit, so zooming in adds more.
  const [svgW, setSvgW] = useState(0);
  useEffect(() => {
    const el = svg.current;
    if (!el || typeof ResizeObserver !== "function") return undefined;
    const read = () => setSvgW(el.getBoundingClientRect().width);
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [proj]);
  const nameScale = svgW ? Math.round((svgW / view.w) * 1000) / 1000 : 0;
  const labels = useMemo(() => (showNames && proj && nameScale > 0
    ? shown.map((f) => ({ name: f.properties.name, spot: areaNameSpot(f.geometry, f.properties.name, proj.fwd, NAME_PX / nameScale, `${proj.H}|${proj.s}`) })).filter((l) => l.spot)
    : []), [showNames, proj, shown, nameScale]);

  // Wheel zoom around the cursor (needs a non-passive listener to stop the page from scrolling).
  useEffect(() => {
    const el = svg.current;
    if (!el || !proj) return undefined;
    const onWheel = (e) => {
      e.preventDefault();
      if (e.shiftKey) { // cycle through the used colors plus one new one; both directions wrap around
        const d = e.deltaY || e.deltaX;
        const now = performance.now();
        if (d && now - shiftAt.current > 120) {
          shiftAt.current = now;
          const { options, sel: at } = cycle.current;
          if (options.length > 1) setSel(options[(options.indexOf(at) + (d > 0 ? 1 : -1) + options.length) % options.length]);
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
  const used = [...new Set(items.filter((i) => i.op !== "erase").map((i) => i.slot))].sort((a, b) => a - b);
  let pending = 0; // the new, unsaved category: the first slot nobody painted with yet
  while (used.includes(pending)) pending += 1;
  const options = [...used, pending];
  const sel = options.includes(picked) ? picked : pending;
  cycle.current = { options, sel };
  const dup = used.some((s) => s !== sel && nameOf(s) === nameOf(sel));

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

  const setImage = (n, uri) => setImages(({ [n]: _gone, ...rest }) => (uri ? { ...rest, [n]: uri } : rest));

  const unit = (view.w / W) * size;

  return <>
    <div className="dm" ref={wrap}>
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
          {shownPaths.map((p) => <path key={p.name} className="dm-res" d={p.d} fillRule="evenodd"
            style={{ fill: p.color, pointerEvents: "none" }}><title>{p.name}</title></path>)}
          {labels.map((l) => <AreaName key={l.name} spot={l.spot} />)}
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
      <button type="button" className="ed-tool" aria-label="Clear" title="Clear all" disabled={!items.length} onClick={() => { setItems([]); setImages({}); }}>×</button>
      <span className={`dm-status ${err && proj ? "err" : "muted"}`}>{proj && err ? err : busy ? "Saving…" : ""}</span>
      {trailing}
    </div>
    <label className="chk-row"><input type="checkbox" checked={colored} onChange={(e) => setColored(e.target.checked)} />
      Paint the areas with different colors</label>
    {proj && <div className="dm-cats">
      <div className="dm-catbar">
        <i className="dm-sw" style={{ background: colorOf(sel) }} />
        <input type="text" value={custom[sel] ?? ""} placeholder={`Unnamed ${sel + 1}`} aria-label="Category name"
          onChange={(e) => setCustom((c) => ({ ...c, [sel]: e.target.value }))} />
        <span className="muted">Shift + scroll to change color</span>
        {dup && <span className="err">Another category has this name: they will merge.</span>}
      </div>
      {used.length > 0 && <div className="clue-chips" role="group" aria-label="Categories">
        {used.map((s) => <button key={s} type="button" className={`fchip ${s === sel ? "on" : ""}`} aria-pressed={s === sel}
          onClick={() => setSel(s)}><i className="dm-sw" style={{ background: colorOf(s) }} />{nameOf(s)}{images[s] && <span aria-hidden="true"> 🖼️</span>}</button>)}
      </div>}
      {used.includes(sel) && <CategoryImage key={sel} name={sel} value={images[sel]} onChange={setImage} />}
    </div>}
  </>;
}
