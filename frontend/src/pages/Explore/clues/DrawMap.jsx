import { useEffect, useMemo, useRef, useState } from "react";
import { api, errorText } from "../../../lib/api.js";
import { DEFAULT_DRAW_LEVEL_SHORTCUTS, sameShortcut } from "../../../lib/shortcut.js";
import { bboxOf, shapes } from "./geoDraw.js";
import { discardStaged, loadImage, stagedUrl } from "./imageTools.js";
import { AreaName, NAME_PX, areaNameSpot } from "./RegionPreview.jsx";
import ClueImage from "./ClueImage.jsx";
import PaintedLayer, { Units } from "./PaintedLayer.jsx";
import { setDrawBusy } from "./drawBusy.js";
import { nameColors } from "./prefixColors.js";
import { createPortal } from "react-dom";
import { streetViewAvailable } from "../../../lib/mapProviders.js";
import { stageFile } from "./imageTools.js";
import { resolveStreetView, streetViewShot } from "./mediaKinds.js";
import { TileBackdrop, TilesIcon, makeProjection } from "./drawTiles.jsx";
import "./draw.css";

const W = 1000;
const MAX_ZOOM = 80;
const DRAG_PX = 4;
const MIN_H = 120; // smallest map height the handle can set (px)
const DEFAULT_H = 336; // height the card gives a map when none is set (keep equal to the .cl-svg max-height in clues.css)
const PALE = "#f6f4e4"; // keep equal to PALE in src/api/routers/clue_draw.py

const ico = { width: 19, height: 19, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const TOOLS = [
  ["fill", "Fill a subdivision", <svg key="f" {...ico}><path d="M5 11l7-7 8 8-7 7z" /><path d="M5 11l8 8" /><path d="M20 17c0 1.5-1 2.5-1 2.5s-1-1-1-2.5a1 1 0 0 1 2 0z" /></svg>],
  ["paint", "Freeform paint", <svg key="p" {...ico}><path d="M18 3l3 3-9 9-3-3z" /><path d="M9 12c-3 0-4 2-4 4s-1 3-2 4c3 1 8 0 8-5" /></svg>],
  ["erase", "Eraser (hold Shift and click to erase a whole subdivision)", <svg key="e" {...ico}><path d="M4 16l9-9 6 6-6 6H8z" /><path d="M8 20h12" /></svg>],
];

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
function CategoryImage({ name, value, look, onChange, onLook }) {
  const ed = useRef(null);
  const col = useRef(null);
  const seq = useRef(0);
  const valueRef = useRef(value);
  valueRef.current = value;
  const [initial] = useState(value ?? null);
  const [gone, setGone] = useState(false);
  const [incoming, setIncoming] = useState(null);
  const [err, setErr] = useState("");
  const blur = !!look?.blur; // blur + height cap live in DrawMap, so they are saved with the area's image
  const maxH = look?.maxH ?? null;
  const setBlur = (b) => onLook(name, { blur: b });
  const setMaxH = (h) => onLook(name, { maxH: h });
  const takeText = async (text) => {
    setErr("");
    try {
      const link = await resolveStreetView(text);
      if (!link) throw new Error("That isn't an image or a Google Maps link.");
      if (!streetViewAvailable()) throw new Error("Street View isn't available with the current map source. Pick one in Settings.");
      setIncoming(await stageFile(await streetViewShot(link)));
    } catch (e) { setErr(e.message); }
  };
  const onState = (has, sig) => {
    const n = ++seq.current;
    if (!has) { if (valueRef.current) onChange(name, null); return; }
    if (sig === ":0") return; // untouched: still the stored image
    ed.current.resolve().then((token) => token && toDataUri(stagedUrl(token)))
      .then((uri) => { if (uri && n === seq.current) onChange(name, uri); }).catch(() => {});
  };
  return <div className="dm-cat" ref={col}>
    <ClueImage ref={ed} current={gone ? null : initial} colRef={col} onState={onState} blur={blur} onBlur={setBlur} maxH={maxH} onMaxH={setMaxH}
      incoming={incoming} onOtherText={takeText} onRemove={() => { setGone(true); onChange(name, null); }} />
    {err && <p className="err">{err}</p>}
  </div>;
}

/** Draw a regional clue as colored categories on its country. Fill = the subdivision you click (level chosen on the map's top-left);
 *  paint = freeform strokes clipped to the outline. Everything you draw goes into the selected category (a color slot, named
 *  "Unnamed N" until renamed in the bar under the toolbar). Shift + wheel cycles through the used colors and one new one
 *  (kept only once something is painted with it). Wheel zooms; drag pans (Fill tool), middle / right button pans with any tool.
 *  The server builds the colored polygons from the list of actions; the result is staged through `stage` and handed to `onResult`
 *  (null when nothing is drawn). `trailing` goes at the end of the toolbar row (the Import | Draw switch).
 *  Lock (toolbar): remembers the zoom and position on screen as `{ k, fx, fy }` (see ZoomableRegion) and hands it to `onView`;
 *  the card then opens on that framing instead of the whole country. `storedView` is the framing already locked (null = none). */
export default function DrawMap({ cc, stage, onResult, trailing, optionsEl, showNames, prefixColors, stored, storedView, onView, maxH, onMaxH }) {
  const [base, setBase] = useState(null);
  const [err, setErr] = useState("");
  const [level, setLevel] = useState(0);
  const [nLevels, setNLevels] = useState(0);
  const [levelKeys, setLevelKeys] = useState(DEFAULT_DRAW_LEVEL_SHORTCUTS);
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
  const [tiles, setTiles] = useState(false); // show the real map behind the drawing
  const [images, setImages] = useState({});
  const [colored, setColored] = useState(true);
  const [colorOpen, setColorOpen] = useState(false);
  const [looks, setLooks] = useState({}); // color slot -> { blur, maxH } of that category's image
  const [locked, setLocked] = useState(storedView ?? null); // { k, fx, fy }: the framing the card opens on
  const viewApplied = useRef(false);
  const [hh, setHh] = useState(null); // live height readout while dragging the height handle
  const hdrag = useRef(null);
  const colorRef = useRef(null);
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
  // "Color by prefix": the colour of each category follows the start of its name (see prefixColors.js).
  const slotColors = useMemo(() => {
    if (!prefixColors || !colored) return null;
    const slots = [...new Set(items.filter((i) => i.op !== "erase").map((i) => i.slot))];
    const cols = nameColors(slots.map(nameOf));
    return cols && new Map(slots.map((s, i) => [s, cols[i]]));
  }, [prefixColors, colored, items, custom]); // eslint-disable-line react-hooks/exhaustive-deps
  const colorOf = (s) => slotColors?.get(s) ?? (palette.length ? palette[s % palette.length] : "#8b98a8");

  useEffect(() => {
    let off = false;
    api("/settings").then((s) => {
      if (!off && Array.isArray(s.draw_level_shortcuts)) setLevelKeys(s.draw_level_shortcuts);
    }).catch(() => {});
    return () => { off = true; };
  }, []);

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
      if (d.levels && level === 0) setLevel(1);
      setPalette(d.colors ?? []);
      setUnits(d.units);
    }).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc, level]);

  // Editing a saved drawing: its areas (and their images) come back as shapes, so they can be repainted, renamed, erased or recolored.
  useEffect(() => {
    if (!stored) return undefined;
    let off = false;
    fetch(`/api/clues/${cc}/geo/${stored}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(r.statusText))))
      .then((g) => {
        if (off) return;
        if (!g.names) { // not made with this tool: all of it becomes one category to repaint or erase
          const coordinates = g.geometries.flatMap((geom) => (geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : []));
          if (coordinates.length) setItems([{ op: "shape", slot: 0, geometry: { type: "MultiPolygon", coordinates } }]);
          return;
        }
        const names = [...new Set(g.names)]; // each saved category takes a color slot, in the order it was saved
        const parts = names.map(() => []);
        g.geometries.forEach((geom, i) => {
          const polys = geom.type === "Polygon" ? [geom.coordinates] : geom.type === "MultiPolygon" ? geom.coordinates : [];
          parts[names.indexOf(g.names[i])].push(...polys);
        });
        setCustom(Object.fromEntries(names.map((n, slot) => [slot, n])));
        setItems(parts.map((coordinates, slot) => ({ op: "shape", slot, geometry: { type: "MultiPolygon", coordinates } })));
        setImages(Object.fromEntries((g.extras ?? []).map((x, i) => [names.indexOf(g.names[i]), x]).filter(([, x]) => x.startsWith("data:image/"))));
        setLooks(Object.fromEntries(g.names.map((n, i) => [names.indexOf(n), { blur: !!g.extra_blurs?.[i], maxH: g.extra_heights?.[i] || null }])));
        setColored(g.colors.some((c) => c.toLowerCase() !== PALE));
      })
      .catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc, stored]);

  useEffect(() => {
    if (!items.length) {
      setBusy(false);
      if (had.current) { had.current = false; setShown([]); resultRef.current(null); }
      return undefined;
    }
    had.current = true;
    let stale = false;
    const ac = new AbortController();
    setBusy(true); setErr("");
    const t = setTimeout(async () => {
      try {
        const sent = items.map(({ d: _d, w: _w, ...i }, k) => {
          const first = items.findIndex((x) => x.slot === i.slot && x.op !== "erase") === k;
          const look = looks[i.slot];
          return { ...i, name: i.op === "erase" ? "" : nameOf(i.slot), ...(first && images[i.slot] ? { extra: images[i.slot], extra_blur: !!look?.blur, extra_max_height: look?.maxH ?? null } : {}) };
        });
        const fc = await api(`/clues/${cc}/draw/build`, { method: "POST", body: { items: sent, colored }, signal: ac.signal });
        if (stale) return;
        setShown(fc.features);
        if (!fc.features.length) { resultRef.current(null); return; }
        const file = new File([JSON.stringify(fc)], "drawing.geojson", { type: "application/geo+json" });
        const up = await stageRef.current({ file });
        if (stale) { discardStaged(up?.token); return; }
        resultRef.current(up);
      } catch (e) { if (!stale) setErr(errorText(e)); } finally { if (!stale) setBusy(false); }
    }, 600);
    return () => { stale = true; clearTimeout(t); ac.abort(); };
  }, [items, images, looks, custom, cc, colored]);
  useEffect(() => { setDrawBusy(busy); }, [busy]);
  useEffect(() => () => setDrawBusy(false), []);

  const proj = useMemo(() => { const bb = base && bboxOf(base); return bb ? makeProjection(bb) : null; }, [base]);
  const landD = useMemo(() => (proj ? shapes(base, proj.fwd).polys : ""), [proj, base]);
  const unitPaths = useMemo(() => (proj && units ? units.map((u) => ({ id: u.id, name: u.name, d: shapes(u.geometry, proj.fwd).polys })) : []), [proj, units]);
  const fillOf = useMemo(() => (colored ? colorOf : () => PALE), [colored, palette, slotColors]); // eslint-disable-line react-hooks/exhaustive-deps
  // Names on the drawn areas (the "Show category names" setting): printed where they fit, so zooming in adds more.
  const [svgBox, setSvgBox] = useState([0, 0]);
  useEffect(() => {
    const el = svg.current;
    if (!el || typeof ResizeObserver !== "function") return undefined;
    const read = () => {
      const r = el.getBoundingClientRect();
      setSvgBox((b) => (b[0] === r.width && b[1] === r.height ? b : [r.width, r.height]));
    };
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [proj]);
  // The map is fitted inside its box (height cap), so the tighter side sets the screen px per map unit.
  const nameScale = svgBox[0] && proj ? Math.round(Math.min(svgBox[0] / view.w, svgBox[1] / ((view.w * proj.H) / W)) * 1000) / 1000 : 0;
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
          if (options.length > 1) setSel(options[(options.indexOf(at) + (d > 0 ? -1 : 1) + options.length) % options.length]);
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
    if (!colorOpen) return undefined;
    const onDown = (e) => { if (colorRef.current && !colorRef.current.contains(e.target)) setColorOpen(false); };
    const onKey = (e) => { if (e.key === "Escape") setColorOpen(false); };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("pointerdown", onDown); document.removeEventListener("keydown", onKey); };
  }, [colorOpen]);

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
    if (!proj || !locked || viewApplied.current) return undefined;
    viewApplied.current = true;
    const w = W / locked.k;
    setView(clampView({ w, x: locked.fx * W - w / 2, y: locked.fy * proj.H - (w * proj.H) / W / 2 }, proj.H));
    return undefined;
  }, [proj]); // eslint-disable-line react-hooks/exhaustive-deps

  // Lock = keep the zoom and position on screen right now: the card then opens on that framing, not on the whole country.
  // Fractions of the map (same as ZoomableRegion), so they don't depend on the screen size.
  const toggleLock = () => {
    if (locked) { setLocked(null); onView?.(null); return; }
    const next = {
      k: Math.round((W / view.w) * 1000) / 1000,
      fx: Math.round(((view.x + view.w / 2) / W) * 10000) / 10000,
      fy: Math.round(((view.y + (view.w * proj.H) / W / 2) / proj.H) * 10000) / 10000,
    };
    setLocked(next);
    onView?.(next);
  };
  const lockBox = locked && proj
    ? clampView({ w: W / locked.k, x: locked.fx * W - W / locked.k / 2, y: locked.fy * proj.H - proj.H / locked.k / 2 }, proj.H)
    : null;

  const painting = tool !== "fill";
  const used = [...new Set(items.filter((i) => i.op !== "erase").map((i) => i.slot))].sort((a, b) => a - b);
  let pending = 0; // the new, unsaved category: the first slot nobody painted with yet
  while (used.includes(pending)) pending += 1;
  const options = [...used, pending];
  const sel = options.includes(picked) ? picked : pending;
  cycle.current = { options, sel };
  const dup = used.some((s) => s !== sel && nameOf(s) === nameOf(sel));
  const pickRef = useRef(null);
  // Bucket fill: the server works out the connected empty area (or the clicked colour's area) inside the division under the click.
  pickRef.current = async (u, e) => {
    const p = toSvg(e);
    if (!p) return;
    const slot = sel;
    try {
      const sent = items.map(({ d: _d, w: _w, ...i }) => ({ ...i, name: i.op === "erase" ? "" : nameOf(i.slot) }));
      const r = await api(`/clues/${cc}/draw/fill`, { method: "POST", body: { items: sent, level, id: u.id, point: proj.inv(p) } });
      setItems((it) => [...it, { op: "shape", slot, geometry: r.geometry }]);
    } catch (ex) { setErr(errorText(ex)); }
  };
  const onPick = useMemo(() => (u, e) => pickRef.current(u, e), []);

  const toSvg = (e) => {
    const m = svg.current.getScreenCTM();
    if (!m) return null;
    const p = new DOMPoint(e.clientX, e.clientY).matrixTransform(m.inverse());
    return [p.x, p.y];
  };

  // Shift + eraser on a subdivision works like the fill bucket: it removes only the connected painted part under the click.
  const eraseIn = async (id, p) => {
    try {
      const sent = items.map(({ d: _d, w: _w, ...i }) => ({ ...i, name: i.op === "erase" ? "" : nameOf(i.slot) }));
      const r = await api(`/clues/${cc}/draw/fill`, { method: "POST", body: { items: sent, level, id, point: proj.inv(p), erase: true } });
      if (r.geometry) setItems((it) => [...it, { op: "erase", name: "", level, id, geometry: r.geometry }]);
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
      const r = svg.current.getBoundingClientRect(); // fitted inside the box: the tighter side sets the scale
      const k = Math.max(d.v.w / r.width, (d.v.w * proj.H) / W / r.height);
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

  const setImage = (n, uri) => setImages(({ [n]: _gone, ...rest }) => (uri ? { ...rest, [n]: uri } : rest));
  const setLook = (n, patch) => setLooks((l) => ({ ...l, [n]: { ...l[n], ...patch } }));

  const unit = (view.w / W) * size;

  // The map's height on the card is the height of this box: drag the bottom handle to set it (the card then shows the same).
  const cap = maxH ?? DEFAULT_H;
  const fullH = () => { const r = svg.current?.getBoundingClientRect(); return r && proj ? Math.round((r.width * proj.H) / W) : 9999; };
  const hDown = (e) => {
    e.preventDefault();
    e.currentTarget.setPointerCapture(e.pointerId);
    hdrag.current = { y: e.clientY, h: svg.current.getBoundingClientRect().height };
  };
  const hMove = (e) => {
    const d = hdrag.current;
    if (!d) return;
    const h = Math.min(Math.max(Math.round(d.h + e.clientY - d.y), MIN_H), fullH());
    setHh(h);
    onMaxH?.(h);
  };
  const hUp = () => { hdrag.current = null; setHh(null); };
  const hKey = (e) => {
    if (e.key !== "ArrowUp" && e.key !== "ArrowDown") return;
    e.preventDefault();
    onMaxH?.(Math.min(Math.max(cap + (e.key === "ArrowUp" ? -20 : 20), MIN_H), fullH()));
  };

  return <>
    <div className={`dm ${tiles ? "tiles" : ""}`} ref={wrap}>
      {proj ? <>
        {tiles && <TileBackdrop proj={proj} view={view} />}
        <div className="dm-levels" role="group" aria-label="Subdivision level">
          {(nLevels ? Array.from({ length: nLevels }, (_, i) => [i + 1, `Adm ${i + 1}`]) : [[0, "National"]]).map(([v, l]) =>
            <button key={l} type="button" className={level === v ? "on" : ""} aria-pressed={level === v} onClick={() => setLevel(v)}>{l}</button>)}
        </div>
        <svg ref={svg} className="dm-svg" viewBox={`${view.x} ${view.y} ${view.w} ${(view.w * proj.H) / W}`} role="img" aria-label="Map to draw on"
          style={{ cursor: painting ? "crosshair" : undefined, maxHeight: tiles ? undefined : cap }} onContextMenu={(e) => e.preventDefault()}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <path className="cl-r" d={landD} fillRule="evenodd" />
          <Units paths={unitPaths} click={tool === "fill"} onPick={onPick} />
          <g className="dm-paint"><PaintedLayer items={items} fwd={proj.fwd} H={proj.H} land={landD} fillOf={fillOf} /></g>
          {labels.map((l) => <AreaName key={l.name} spot={l.spot} />)}
          {lockBox && <rect x={lockBox.x} y={lockBox.y} width={lockBox.w} height={(lockBox.w * proj.H) / W} fill="none" stroke="#ffd04d"
            strokeWidth="2" strokeDasharray="8 5" vectorEffect="non-scaling-stroke" style={{ pointerEvents: "none" }} />}
          {live && <path d={pathOf(live)} fill="none" strokeLinecap="round" strokeLinejoin="round" strokeWidth={unit}
            stroke={tool === "erase" ? "#ff7357" : "#ffffff"} strokeOpacity=".5" style={{ pointerEvents: "none" }} />}
        </svg>
        {!tiles && <>
          {hh != null && <span className="ed-hval">{hh} px</span>}
          <div className={`ed-hh ${maxH ? "set" : ""} ${hh != null ? "on" : ""}`} role="slider" tabIndex={0} aria-orientation="vertical"
            aria-label="Maximum map height" aria-valuemin={MIN_H} aria-valuenow={cap} title="Drag to set the map height (double-click to reset)"
            onPointerDown={hDown} onPointerMove={hMove} onPointerUp={hUp} onKeyDown={hKey} onDoubleClick={() => onMaxH?.(null)} />
        </>}
      </> : err ? <p className="err">{err}</p> : <p className="muted">Loading map…</p>}
    </div>
    <div className="ed-tools dm-tools" role="toolbar" aria-label="Draw tools">
      {TOOLS.map(([k, label, icon]) => <button key={k} type="button" className="ed-tool" aria-pressed={tool === k} aria-label={label} title={label}
        onClick={() => setTool(k)}>{icon}</button>)}
      {painting && <input type="range" min="4" max="80" value={size} aria-label="Brush size" title="Brush size" onChange={(e) => setSize(Number(e.target.value))} />}
      <i className="ed-sep" />
      <button type="button" className="ed-tool" aria-label="Undo" title="Undo" disabled={!items.length} onClick={() => setItems((it) => it.slice(0, -1))}>↶</button>
      <button type="button" className="ed-tool" aria-label="Clear" title="Clear all" disabled={!items.length} onClick={() => { setItems([]); setImages({}); setLooks({}); }}>×</button>
      <button type="button" className="ed-tool" aria-pressed={!!locked} aria-label={locked ? "Unlock view" : "Lock view"} disabled={!proj}
        title={locked ? "Unlock view (the card shows the whole map)" : "Lock view: the card opens on the zoom and position on screen now"} onClick={toggleLock}>
        <svg {...ico}><rect x="5" y="11" width="14" height="9" rx="2" /><path d={locked ? "M8 11V8a4 4 0 0 1 8 0v3" : "M8 11V8a4 4 0 0 1 7.5-2"} /></svg></button>
      <button type="button" className="ed-tool" aria-pressed={tiles} aria-label="Show the map behind the drawing" disabled={!proj}
        title="Show the map behind the drawing" onClick={() => setTiles((v) => !v)}><TilesIcon /></button>
      <span className={`dm-status ${err && proj ? "err" : "muted"}`}>{proj && err ? err : busy ? "Saving…" : ""}</span>
      {trailing}
    </div>
    {optionsEl && createPortal(<label className="chk-row"><input type="checkbox" checked={colored} onChange={(e) => setColored(e.target.checked)} />
      Paint the areas with different colors</label>, optionsEl)}
    {proj && <div className="dm-cats">
      <div className="dm-catbar">
        <div className="dm-color" ref={colorRef}>
          <button type="button" className={`dm-color-btn ${used.length > 0 ? "has-menu" : ""}`} aria-haspopup="menu" aria-expanded={colorOpen} aria-label="Pick a color" title="Pick a color"
            onClick={() => setColorOpen((o) => !o)}><i className="dm-sw" style={{ background: colorOf(sel) }} /></button>
          {colorOpen && used.length > 0 && <div className="dm-menu" role="menu" aria-label="Colors">
            {used.map((s) => <button key={s} type="button" role="menuitemradio" aria-checked={s === sel} className={`dm-menu-item ${s === sel ? "on" : ""}`}
              onClick={() => { setSel(s); setColorOpen(false); }}><i className="dm-sw" style={{ background: colorOf(s) }} />{nameOf(s)}{images[s] && <span aria-hidden="true"> 🖼️</span>}</button>)}
          </div>}
        </div>
        <input type="text" value={custom[sel] ?? ""} placeholder={`Unnamed ${sel + 1}`} aria-label="Category name"
          onChange={(e) => setCustom((c) => ({ ...c, [sel]: e.target.value }))} />
        <span className="muted">Shift + scroll to change color</span>
        {dup && <span className="err">Another category has this name: they will merge.</span>}
      </div>
      {used.includes(sel) && <CategoryImage key={sel} name={sel} value={images[sel]} look={looks[sel]} onChange={setImage} onLook={setLook} />}
    </div>}
  </>;
}
