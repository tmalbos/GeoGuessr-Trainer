import { useEffect, useMemo, useRef, useState } from "react";
import { DEFAULTS, PRESETS, STYLES, generate, randomParams } from "../../lib/streetgen.js";
import { num } from "../../lib/format.js";
import "./minigames.css";

const pct = (v) => `${Math.round(v * 100)}%`;
const off = (v) => (v === 0 ? "Off" : v);
const STYLE_LABELS = { grid: "Grid", organic: "Organic", suburb: "Suburban loops", radial: "Radial" };
const STYLE_COLORS = { grid: "#6cc0e5", organic: "#3fb97a", suburb: "#cdbf93", radial: "#ff7357" };

// [param, label, min, max, step, format, hint]
const GROUPS = [
  ["Scale", [
    ["sizeKm", "Map size", 3, 16, 0.5, (v) => `${v} km`, "Width of the generated square."],
    ["block", "Block size", 50, 250, 5, (v) => `${v} m`, "Typical distance between local streets."],
  ]],
  ["Neighbourhoods", [
    ["districts", "Neighbourhoods", 1, 40, 1, String, "Each one gets its own style, orientation and block size."],
    ["centreSize", "Centre size", 0.05, 1, 0.05, pct, "Size of the central neighbourhood relative to the city."],
    ["wGrid", "Grid share", 0, 1, 0.05, pct, "How common planned grids are."],
    ["wOrganic", "Organic share", 0, 1, 0.05, pct, "How common irregular, hand-grown streets are."],
    ["wSuburb", "Suburban share", 0, 1, 0.05, pct, "How common curvy suburbs with cul-de-sacs are."],
    ["wRadial", "Radial share", 0, 1, 0.05, pct, "How common star / roundabout layouts are."],
    ["alignment", "Grid alignment", 0, 1, 0.05, pct, "High = neighbouring grids share the main orientation."],
  ]],
  ["Character", [
    ["regularity", "Regularity", 0, 1, 0.05, pct, "Low = streets wander and cross at odd angles."],
    ["curviness", "Curviness", 0, 1, 0.05, pct, "How strongly streets bend."],
    ["aspect", "Block elongation", 1, 2.5, 0.1, (v) => `×${v.toFixed(1)}`, "Long thin blocks versus square ones."],
    ["connectivity", "Connectivity", 0, 1, 0.05, pct, "Low = more dead ends and cul-de-sacs."],
  ]],
  ["Footprint", [
    ["falloff", "Thinner towards edges", 0, 1, 0.05, pct, "Blocks grow and streets thin out away from the centre."],
    ["patchiness", "Empty patches", 0, 1, 0.05, pct, "Parks, hills and wasteland with no local streets."],
    ["stretch", "Stretch", 1, 3, 0.1, (v) => `×${v.toFixed(1)}`, "Elongates the city (coastal / valley cities)."],
    ["stretchAngle", "Footprint angle", -90, 90, 5, (v) => `${v}°`, "Direction of the stretch, and of a squarish outline."],
    ["squareness", "Squareness", 0, 1, 0.05, pct, "0 = round city, 1 = square city."],
    ["gridAngle", "Main grid angle", 0, 90, 5, (v) => `${v}°`, "Orientation of the central grid."],
  ]],
  ["Main roads", [
    ["collectorEvery", "Collector every N blocks", 0, 12, 1, off, "Secondary roads that collect local traffic. 0 = none."],
    ["hubs", "Sub-centres", 0, 14, 1, off, "Extra hubs linked to the centre by arterials."],
    ["exits", "Roads to the edge", 0, 10, 1, off, "Arterials leaving the city."],
    ["spokes", "Radial roads", 0, 10, 1, off, "Straight arterials from the centre (La Plata's diagonals)."],
    ["spokeAngle", "Radial road angle", 0, 90, 5, (v) => `${v}°`, "Rotation of the radial roads relative to the main grid."],
    ["ring", "Ring road", 0, 1, 0.05, (v) => (v === 0 ? "Off" : pct(v)), "Ring road radius relative to the city. 0 = none."],
  ]],
];

const fitView = (H, w, h) => {
  const s = Math.min(w, h) / (H * 2 * 1.04);
  return { s, x: w / 2, y: h / 2 };
};
const niceLen = (s) => [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((l) => l * s >= 70) ?? 5000;

const CLASSES = [
  [0, "#6f7f93", 6, 1],
  [1, "#b4bfcc", 10, 1.8],
  [2, "#e0cf9a", 16, 2.8],
];

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

  useEffect(() => { setView(fitView(map.H, size.w, size.h)); }, [fitKey, size.w, size.h]); // eslint-disable-line react-hooks/exhaustive-deps

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

    if (show.districts) {
      g.lineWidth = 1.5 / view.s;
      for (const d of map.districts) {
        g.strokeStyle = STYLE_COLORS[d.style] + "aa"; g.fillStyle = STYLE_COLORS[d.style];
        g.beginPath();
        for (let i = 0; i <= 48; i++) {
          const t = (i / 48) * Math.PI * 2, c0 = Math.cos(t), s0 = Math.sin(t);
          const nrm = d.pw === 2 ? 1 : (Math.abs(c0) ** d.pw + Math.abs(s0) ** d.pw) ** (1 / d.pw);
          const u = (c0 / nrm) * d.r * d.ex, v = (s0 / nrm) * d.r / d.ex;
          const x = d.x + u * Math.cos(d.a) - v * Math.sin(d.a), y = d.y + u * Math.sin(d.a) + v * Math.cos(d.a);
          if (i) g.lineTo(x, y); else g.moveTo(x, y);
        }
        g.stroke();
        g.beginPath(); g.arc(d.x, d.y, 6 / view.s, 0, 6.2832); g.fill();
      }
    }

    for (const [cls, color, widthM, minPx] of CLASSES) {
      g.beginPath();
      for (const e of edges) {
        if (e.cls !== cls) continue;
        const p = e.pts;
        g.moveTo(p[0], p[1]);
        for (let i = 2; i < p.length; i += 2) g.lineTo(p[i], p[i + 1]);
      }
      g.lineWidth = Math.max(minPx, widthM * view.s) / view.s;
      g.strokeStyle = color;
      g.stroke();
    }
    const dots = (test, color, px) => {
      g.fillStyle = color;
      g.beginPath();
      for (const n of nodes) if (test(n)) { g.moveTo(n.x + px / view.s, n.y); g.arc(n.x, n.y, px / view.s, 0, 6.2832); }
      g.fill();
    };
    if (show.nodes) dots((n) => n.deg >= 3, "#6cc0e5", 2.2);
    if (show.deadEnds) dots((n) => n.deg === 1 && !n.border, "#ff7357", 3);

    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const len = niceLen(view.s), px = len * view.s, x0 = 16, y0 = size.h - 18;
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
  const [live, setLive] = useState(params);
  const [preset, setPreset] = useState(PRESETS[0].key);
  const [show, setShow] = useState({ nodes: false, deadEnds: true, districts: false });
  const [resets, setResets] = useState(0);

  // Generating takes a moment on big maps, so wait until the sliders stop moving.
  useEffect(() => { const t = setTimeout(() => setLive(params), 250); return () => clearTimeout(t); }, [params]);
  const map = useMemo(() => generate(live), [live]);
  const stale = live !== params;
  const st = map.stats;

  const setNum = (k) => (e) => { const v = Number(e.target.value); setParams((p) => ({ ...p, [k]: v })); setPreset("custom"); };
  const apply = (key, p) => { setParams({ ...DEFAULTS, ...p }); setPreset(key); };
  const flag = (k) => (e) => setShow((s) => ({ ...s, [k]: e.target.checked }));

  return <div className="mg">
    <aside className="mg-panel">
      <h3>Style</h3>
      <div className="mg-presets">
        {PRESETS.map((p) => <button key={p.key} type="button" className={preset === p.key ? "on" : ""} onClick={() => apply(p.key, p.params)}>{p.label}</button>)}
        <button type="button" className={preset === "random" ? "on" : ""} onClick={() => apply("random", randomParams())}>Surprise me</button>
      </div>

      <h3>Seed</h3>
      <div className="mg-seed">
        <input type="number" aria-label="Seed" value={params.seed} onChange={setNum("seed")} />
        <button type="button" className="btn ghost" onClick={() => { setParams((p) => ({ ...p, seed: Math.floor(Math.random() * 99999) })); setPreset("custom"); }}>New seed</button>
      </div>

      {GROUPS.map(([title, items]) => <section key={title}>
        <h3>{title}</h3>
        {title === "Neighbourhoods" && <div className="mg-row">
          <label htmlFor="mg-centreStyle">Centre style</label>
          <select id="mg-centreStyle" value={params.centreStyle} onChange={(e) => { setParams((p) => ({ ...p, centreStyle: e.target.value })); setPreset("custom"); }}>
            {STYLES.map((s) => <option key={s} value={s}>{STYLE_LABELS[s]}</option>)}
          </select>
        </div>}
        {items.map(([k, label, min, max, step, fmt, hint]) => <div className="mg-row" key={k} title={hint}>
          <label htmlFor={`mg-${k}`}>{label}<output>{fmt(params[k])}</output></label>
          <input id={`mg-${k}`} type="range" min={min} max={max} step={step} value={params[k]} onChange={setNum(k)} />
        </div>)}
      </section>)}
    </aside>

    <div className="mg-view">
      {st && <div className="mg-stats">
        <div><b>{num(Math.round(st.km))} km</b><span>of road ({num(Math.round(st.kmMain))} km main)</span></div>
        <div><b>{num(st.nodes)}</b><span>junctions and ends</span></div>
        <div><b>{Math.round(st.avgEdge)} m</b><span>average segment</span></div>
        <div><b>{num(st.intersections)}</b><span>intersections</span></div>
        <div><b>{num(st.deadEnds)}</b><span>local dead ends</span></div>
        <div><b>{st.mainDeadEnds}</b><span>main-road dead ends</span></div>
        <div><b>{st.density.toFixed(1)}</b><span>km of road per km²</span></div>
        <div><b>{st.ms} ms</b><span>to generate</span></div>
      </div>}
      <div className="mg-tools">
        <button type="button" className="btn ghost" onClick={() => setResets((n) => n + 1)}>Reset view</button>
        <label className="mg-check"><input type="checkbox" checked={show.deadEnds} onChange={flag("deadEnds")} />Show dead ends</label>
        <label className="mg-check"><input type="checkbox" checked={show.nodes} onChange={flag("nodes")} />Show intersections</label>
        <label className="mg-check"><input type="checkbox" checked={show.districts} onChange={flag("districts")} />Show neighbourhoods</label>
      </div>
      <div className={`mg-canvas-wrap ${stale ? "stale" : ""}`}>
        <MapCanvas map={map} show={show} fitKey={`${live.sizeKm}-${resets}`} />
      </div>
    </div>
  </div>;
}
