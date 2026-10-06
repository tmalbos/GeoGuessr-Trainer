import { useCallback, useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import { DEFAULTS, PRESETS, generate, randomParams } from "../../lib/streetgen.js";
import { num } from "../../lib/format.js";
import "./minigames.css";

const pct = (v) => `${Math.round(v * 100)}%`;
const off = (v) => (v === 0 ? "Off" : v);

// [param, label, min, max, step, format, hint]
const GROUPS = [
  ["Shape and scale", [
    ["sizeKm", "City diameter", 1, 8, 0.5, (v) => `${v} km`, "How big the city is."],
    ["block", "Block size", 50, 250, 5, (v) => `${v} m`, "Distance between neighbouring streets."],
    ["rotation", "Grid rotation", 0, 90, 1, (v) => `${v}°`, "Orientation of the street lattice."],
    ["squareness", "Squareness", 0, 1, 0.05, pct, "0 = round city, 1 = square city."],
    ["shape", "Outline irregularity", 0, 0.6, 0.05, pct, "How lumpy the city edge is."],
  ]],
  ["Messiness", [
    ["jitter", "Intersection jitter", 0, 1, 0.05, pct, "Random shift of every intersection."],
    ["warp", "Warp", 0, 1, 0.05, pct, "Smooth large-scale bending of the whole grid."],
    ["curve", "Street curvature", 0, 1, 0.05, pct, "How much individual streets bow."],
    ["connectivity", "Connectivity", 0, 1, 0.05, pct, "100% = full grid. Low = tree-like with many dead ends."],
    ["falloff", "Sparser at the edges", 0, 1, 0.05, pct, "How much the street network thins out away from the centre."],
    ["diagRandom", "Random diagonals", 0, 0.6, 0.05, pct, "Shortcuts that cut across blocks."],
  ]],
  ["Main roads", [
    ["avenueEvery", "Avenue every N blocks", 0, 12, 1, off, "Straight main roads on the grid. 0 = none."],
    ["diagAves", "Diagonal avenues", 0, 5, 1, off, "Diagonal main roads crossing the centre, like La Plata."],
    ["radials", "Radial roads", 0, 10, 1, off, "Main roads from the centre out to the edge of town."],
  ]],
];

const fitView = (map, w, h) => {
  const b = map.bounds, ext = Math.max(b.maxx - b.minx, b.maxy - b.miny, 1);
  const s = Math.min(w, h) / (ext * 1.08);
  return { s, x: w / 2 - ((b.minx + b.maxx) / 2) * s, y: h / 2 - ((b.miny + b.maxy) / 2) * s };
};

const niceLen = (m) => [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((l) => l * m.s >= 70) ?? 5000;

function MapCanvas({ map, show, fitKey }) {
  const wrap = useRef(null);
  const cv = useRef(null);
  const drag = useRef(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  // Re-fit when the city size changes, on "Reset view", or when the panel resizes.
  useEffect(() => { setView(fitView(map, size.w, size.h)); }, [fitKey, size.w, size.h]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = cv.current;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      setView((v) => {
        const s = Math.min(Math.max(v.s * Math.exp(-e.deltaY * 0.0015), 0.01), 30), f = s / v.s;
        return { s, x: mx - (mx - v.x) * f, y: my - (my - v.y) * f };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const c = cv.current, dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const g = c.getContext("2d");
    g.fillStyle = "#0d1117";
    g.fillRect(0, 0, c.width, c.height);
    g.setTransform(dpr * view.s, 0, 0, dpr * view.s, dpr * view.x, dpr * view.y);
    g.lineCap = "round"; g.lineJoin = "round";
    const { nodes, edges } = map;
    const stroke = (main, color, widthM, minPx) => {
      g.beginPath();
      for (const e of edges) {
        if (e.main !== main) continue;
        const a = nodes[e.a], b = nodes[e.b];
        g.moveTo(a.x, a.y);
        if (e.curved) g.quadraticCurveTo(e.cx, e.cy, b.x, b.y); else g.lineTo(b.x, b.y);
      }
      g.lineWidth = Math.max(minPx, widthM * view.s) / view.s;
      g.strokeStyle = color;
      g.stroke();
    };
    stroke(false, "#7d8da0", 7, 1);
    stroke(true, "#e0cf9a", 13, 2);
    const dots = (test, color, px) => {
      g.fillStyle = color;
      g.beginPath();
      for (const n of nodes) if (test(n)) { g.moveTo(n.x + px / view.s, n.y); g.arc(n.x, n.y, px / view.s, 0, 6.2832); }
      g.fill();
    };
    if (show.nodes) dots((n) => n.deg >= 3, "#6cc0e5", 2.2);
    if (show.deadEnds) dots((n) => n.deg === 1, "#ff7357", 3);

    // scale bar (screen space)
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const len = niceLen(view), px = len * view.s, x0 = 16, y0 = size.h - 18;
    g.strokeStyle = "#e6edf3"; g.lineWidth = 2; g.lineCap = "butt";
    g.beginPath(); g.moveTo(x0, y0 - 5); g.lineTo(x0, y0); g.lineTo(x0 + px, y0); g.lineTo(x0 + px, y0 - 5); g.stroke();
    g.fillStyle = "#e6edf3"; g.font = "12px 'Bricolage Grotesque', system-ui, sans-serif";
    g.fillText(len >= 1000 ? `${len / 1000} km` : `${len} m`, x0, y0 - 9);
  }, [map, view, size, show]);

  const down = (e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY }; };
  const move = (e) => {
    const d = drag.current;
    if (!d) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    drag.current = { x: e.clientX, y: e.clientY };
    setView((v) => ({ ...v, x: v.x + dx, y: v.y + dy }));
  };
  const up = () => { drag.current = null; };

  return <>
    <div ref={wrap} style={{ position: "absolute", inset: 0 }}>
      <canvas ref={cv} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}
        role="img" aria-label="Generated street network" />
    </div>
    <span className="mg-hint">Drag to pan · scroll to zoom</span>
  </>;
}

export default function StreetGenerator() {
  const [params, setParams] = useState({ ...DEFAULTS, ...PRESETS[0].params });
  const [preset, setPreset] = useState(PRESETS[0].key);
  const [show, setShow] = useState({ nodes: false, deadEnds: true });
  const [resets, setResets] = useState(0);

  const deferred = useDeferredValue(params);
  const map = useMemo(() => generate(deferred), [deferred]);
  const stale = deferred !== params;
  const st = map.stats;

  const set = (k) => (e) => { const v = Number(e.target.value); setParams((p) => ({ ...p, [k]: v })); setPreset("custom"); };
  const apply = (key, p) => { setParams({ ...DEFAULTS, ...p }); setPreset(key); };
  const reroll = useCallback(() => setParams((p) => ({ ...p, seed: Math.floor(Math.random() * 99999) })), []);

  return <div className="mg">
    <aside className="mg-panel">
      <h3>Style</h3>
      <div className="mg-presets">
        {PRESETS.map((p) => <button key={p.key} type="button" className={preset === p.key ? "on" : ""} onClick={() => apply(p.key, p.params)}>{p.label}</button>)}
        <button type="button" className={preset === "random" ? "on" : ""} onClick={() => apply("random", randomParams())}>Surprise me</button>
      </div>

      <h3>Seed</h3>
      <div className="mg-seed">
        <input type="number" aria-label="Seed" value={params.seed} onChange={set("seed")} />
        <button type="button" className="btn ghost" onClick={reroll}>New seed</button>
      </div>

      {GROUPS.map(([title, items]) => <section key={title}>
        <h3>{title}</h3>
        {items.map(([k, label, min, max, step, fmt, hint]) => <div className="mg-row" key={k} title={hint}>
          <label htmlFor={`mg-${k}`}>{label}<output>{fmt(params[k])}</output></label>
          <input id={`mg-${k}`} type="range" min={min} max={max} step={step} value={params[k]} onChange={set(k)} />
        </div>)}
      </section>)}
    </aside>

    <div className="mg-view">
      {st && <div className="mg-stats">
        <div><b>{num(st.nodes)}</b><span>nodes</span></div>
        <div><b>{num(Math.round(st.km))} km</b><span>of road ({num(Math.round(st.mainKm))} km main)</span></div>
        <div><b>{Math.round(st.avgEdge)} m</b><span>average segment</span></div>
        <div><b>{num(st.intersections)}</b><span>intersections</span></div>
        <div><b>{num(st.deadEnds)}</b><span>dead ends</span></div>
        <div><b>{st.density.toFixed(1)}</b><span>km of road per km²</span></div>
      </div>}
      <div className="mg-tools">
        <button type="button" className="btn ghost" onClick={() => setResets((n) => n + 1)}>Reset view</button>
        <label className="mg-check"><input type="checkbox" checked={show.deadEnds} onChange={(e) => setShow((s) => ({ ...s, deadEnds: e.target.checked }))} />Show dead ends</label>
        <label className="mg-check"><input type="checkbox" checked={show.nodes} onChange={(e) => setShow((s) => ({ ...s, nodes: e.target.checked }))} />Show intersections</label>
      </div>
      <div className={`mg-canvas-wrap ${stale ? "stale" : ""}`}>
        <MapCanvas map={map} show={show} fitKey={`${deferred.sizeKm}-${resets}`} />
      </div>
    </div>
  </div>;
}
