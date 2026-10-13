import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { num as num2 } from "../../lib/format.js";
import { DEFAULT_PARAMS, DEMOS, RULE_TEXT, buildDemo, generateCity, summarize } from "../../lib/streetgen/index.js";
import "./minigames.css";

// Viewer for the new city generator (organic style so far) and its validator. The hand-built demo cities of
// phase 1 live in the collapsed "Debug" section. World coordinates are metres, +y north; the canvas flips y.

const COLORS = { tertiary: "#6f7f93", secondary: "#b4bfcc", primary: "#e0cf9a", highway: "#ff7357", ramp: "#ff9d86" };
const MIN_PX = { tertiary: 1, secondary: 1.8, primary: 2.8, highway: 3.4, ramp: 1.8 };
const ORDER = ["tertiary", "secondary", "primary", "highway"];
const kindOf = (e) => (e.highway ? "highway" : e.ramp ? "ramp" : e.cls);
const roadColor = (id) => `hsl(${(id * 137.5) % 360} 65% 62%)`;

const fitView = (H, w, h) => ({ s: Math.min(w, h) / (H * 2 * 1.04), x: w / 2, y: h / 2 });
const niceLen = (s) => [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((l) => l * s >= 70) ?? 5000;

function MapCanvas({ city, show, fitKey, focus }) {
  const wrap = useRef(null);
  const cv = useRef(null);
  const drag = useRef(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });
  const H = (city.bounds.maxx - city.bounds.minx) / 2;

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  useEffect(() => { setView(fitView(H, size.w, size.h)); }, [fitKey, size.w, size.h]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!focus) return;
    setView((v) => { const s = Math.max(v.s, 0.5); return { s, x: size.w / 2 - focus.x * s, y: size.h / 2 + focus.y * s }; });
  }, [focus]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = cv.current;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      setView((v) => {
        const s = Math.min(Math.max(v.s * Math.exp(-e.deltaY * 0.0015), 0.02), 40), f = s / v.s;
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
    g.fillStyle = "#0a0e13";
    g.fillRect(0, 0, c.width, c.height);
    const sx = (x) => x * view.s + view.x, sy = (y) => -y * view.s + view.y;

    // world space (metres, y up)
    g.setTransform(dpr * view.s, 0, 0, -dpr * view.s, dpr * view.x, dpr * view.y);
    g.lineCap = "round"; g.lineJoin = "round";
    const b = city.bounds;
    g.fillStyle = "#0d1117";
    g.fillRect(b.minx, b.miny, b.maxx - b.minx, b.maxy - b.miny);
    if (show.footprint && city.footprint) {
      g.strokeStyle = "#3a4656"; g.lineWidth = 1.5 / view.s; g.setLineDash([8 / view.s, 6 / view.s]);
      g.beginPath(); city.footprint.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y))); g.closePath(); g.stroke();
      g.setLineDash([]);
    }
    for (const w of city.water) {
      g.fillStyle = "#1a4562";
      g.beginPath();
      w.polygon.forEach(([x, y], i) => (i ? g.lineTo(x, y) : g.moveTo(x, y)));
      g.closePath(); g.fill();
    }
    for (const r of city.rails) {
      const path = () => { g.beginPath(); for (let i = 0; i < r.pts.length; i += 2) (i ? g.lineTo(r.pts[i], r.pts[i + 1]) : g.moveTo(r.pts[i], r.pts[i + 1])); };
      g.lineCap = "butt";
      path(); g.strokeStyle = "#8b98a8"; g.lineWidth = Math.max(2.2, r.width * view.s) / view.s; g.setLineDash([]); g.stroke();
      path(); g.strokeStyle = "#0d1117"; g.lineWidth = Math.max(1, r.width * view.s * 0.45) / view.s; g.setLineDash([9 / view.s, 9 / view.s]); g.stroke();
      g.setLineDash([]); g.lineCap = "round";
    }
    const byRoad = show.roads && city.roads.length > 0;
    const stroke = (list, color, wm, minPx) => {
      g.strokeStyle = color; g.lineWidth = Math.max(minPx, wm * view.s) / view.s;
      for (const e of list) {
        g.beginPath(); g.moveTo(e.pts[0], e.pts[1]);
        for (let i = 2; i < e.pts.length; i += 2) g.lineTo(e.pts[i], e.pts[i + 1]);
        g.stroke();
      }
    };
    for (const k of ["tertiary", "secondary", "ramp", "primary", "highway"]) {
      const list = city.edges.filter((e) => kindOf(e) === k);
      if (!list.length) continue;
      if (byRoad) {
        for (const e of list) stroke([e], e.roadId == null ? "#444" : roadColor(e.roadId), e.width, MIN_PX[k]);
        continue;
      }
      const deck = list.filter((e) => e.bridge);
      if (deck.length) stroke(deck, "#05080c", 0, Math.max(MIN_PX[k] + 3, deck[0].width * view.s + 4));
      stroke(list, COLORS[k], list[0].width, MIN_PX[k]);
    }

    // screen space (pixels)
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const dot = (x, y, r, color) => { g.fillStyle = color; g.beginPath(); g.arc(sx(x), sy(y), r, 0, 6.2832); g.fill(); };
    const deg = new Map();
    for (const e of city.edges) { deg.set(e.a, (deg.get(e.a) || 0) + 1); deg.set(e.b, (deg.get(e.b) || 0) + 1); }
    for (const n of city.nodes) {
      if (n.type === "roundabout") {
        g.strokeStyle = "#e6edf3"; g.lineWidth = 2;
        g.beginPath(); g.arc(sx(n.x), sy(n.y), Math.max(5, (n.radius || 25) * view.s), 0, 6.2832); g.stroke();
      } else if (n.type === "signal") dot(n.x, n.y, 4, "#e6b84a");
      else if (n.type === "ramp") dot(n.x, n.y, 4, "#ff9d86");
      else if (show.nodes && deg.get(n.id) >= 3) dot(n.x, n.y, 2.4, "#6cc0e5");
      if (show.deadEnds && n.type === "end") dot(n.x, n.y, 3.2, "#ff7357");
    }
    for (const x of city.crossings) if (x.kind === "overpass") dot(x.x, x.y, 2.5, "#ffffffaa");
    if (show.violations) {
      g.font = "600 12px 'Bricolage Grotesque', system-ui, sans-serif";
      for (const v of city.violations) {
        const col = v.severity === "warn" ? "#e6b84a" : "#ff4d4d";
        g.strokeStyle = col; g.lineWidth = 2;
        g.beginPath(); g.arc(sx(v.x), sy(v.y), 13, 0, 6.2832); g.stroke();
        g.fillStyle = col; g.fillText(v.rule, sx(v.x) + 16, sy(v.y) + 4);
      }
    }
    const len = niceLen(view.s), px = len * view.s, x0 = 16, y0 = size.h - 18;
    g.strokeStyle = "#e6edf3"; g.lineWidth = 2; g.lineCap = "butt";
    g.beginPath(); g.moveTo(x0, y0 - 5); g.lineTo(x0, y0); g.lineTo(x0 + px, y0); g.lineTo(x0 + px, y0 - 5); g.stroke();
    g.fillStyle = "#e6edf3"; g.font = "12px 'Bricolage Grotesque', system-ui, sans-serif";
    g.fillText(len >= 1000 ? `${len / 1000} km` : `${len} m`, x0, y0 - 9);
  }, [city, view, size, show]);

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
        role="img" aria-label="Road network" />
    </div>
    <span className="mg-hint">Drag to pan · scroll to zoom</span>
  </>;
}

const km = (v) => `${v.toFixed(1)} km`;
const STYLE_LABELS = { organic: "Organic", planned: "Planned (phase 3)" };

export default function StreetGenerator() {
  const [params, setParams] = useState({ ...DEFAULT_PARAMS, style: "organic" });
  const [live, setLive] = useState(params);
  const [demo, setDemo] = useState(null);
  const [show, setShow] = useState({ violations: true, deadEnds: false, nodes: false, roads: false, footprint: false });
  const [resets, setResets] = useState(0);
  const [focus, setFocus] = useState(null);

  // Generating takes a moment on big maps, so wait until the controls stop moving.
  useEffect(() => { const t = setTimeout(() => setLive(params), 250); return () => clearTimeout(t); }, [params]);
  const built = useMemo(() => {
    try { return { city: demo ? buildDemo(demo) : generateCity(live) }; } catch (e) { return { error: e.stack || e.message }; }
  }, [live, demo]);
  const stale = !demo && live !== params;

  const set = (k, v) => setParams((p) => ({ ...p, [k]: v }));
  const num = (k) => (e) => set(k, Number(e.target.value));
  const flag = (k) => (e) => setShow((s) => ({ ...s, [k]: e.target.checked }));
  const pickDemo = (k) => { setDemo(k); setFocus(null); setResets((n) => n + 1); };

  if (built.error) return <ErrorBox message="The city could not be generated." detail={built.error} onRetry={() => setLive({ ...params })} />;
  const { city } = built;
  const st = city.stats;
  const errors = city.violations.filter((v) => v.severity === "error");
  const warns = city.violations.length - errors.length;
  const byRule = summarize(city.violations);
  const info = city.meta.info;

  return <div className="mg">
    <aside className="mg-panel">
      {demo && <p className="muted">Showing a hand-built test city. <button type="button" className="link-btn" onClick={() => pickDemo(null)}>Back to the generator</button></p>}
      <h3>City</h3>
      <div className="mg-row">
        <label htmlFor="mg-style">Style</label>
        <select id="mg-style" value={params.style} onChange={(e) => set("style", e.target.value)}>
          {Object.entries(STYLE_LABELS).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      </div>
      <div className="mg-row">
        <label htmlFor="mg-size">Map size<output>{params.sizeKm} km</output></label>
        <input id="mg-size" type="range" min="3" max="16" step="0.5" value={params.sizeKm} onChange={num("sizeKm")} />
      </div>
      <div className="mg-row">
        <label htmlFor="mg-irr">Irregularity<output>{Math.round(params.irregularity * 100)}%</output></label>
        <input id="mg-irr" type="range" min="0" max="1" step="0.05" value={params.irregularity} onChange={num("irregularity")} />
      </div>
      <div className="mg-row">
        <label htmlFor="mg-seed">Seed</label>
        <div className="mg-seed">
          <input id="mg-seed" type="number" value={params.seed} onChange={num("seed")} />
          <button type="button" className="btn ghost" onClick={() => set("seed", Math.floor(Math.random() * 99999))}>New seed</button>
        </div>
      </div>
      <p className="muted mg-note">Water, highway and rail switches arrive with phases 4 to 6.</p>

      <h3>Validator</h3>
      <p className="mg-verdict">
        <span className={`badge ${errors.length ? "bad" : "on"}`}>{errors.length ? `${errors.length} violation${errors.length === 1 ? "" : "s"}` : "No violations"}</span>
        {warns > 0 && <span className="badge warn">{warns} warning{warns === 1 ? "" : "s"}</span>}
        {city.meta.attempts > 1 && <span className="badge">{city.meta.attempts} attempts</span>}
      </p>
      {Object.keys(byRule).length > 0 && <ul className="mg-viol">
        {city.violations.slice(0, 60).map((v, i) => <li key={i}>
          <button type="button" className={v.severity} title={RULE_TEXT[v.rule]} onClick={() => setFocus({ x: v.x, y: v.y, n: Math.random() })}>
            <b>{v.rule}</b> {v.message}
          </button>
        </li>)}
        {city.violations.length > 60 && <li className="muted">…and {city.violations.length - 60} more</li>}
      </ul>}

      <details className="mg-debug">
        <summary>Debug</summary>
        <label className="mg-check"><input type="checkbox" checked={show.violations} onChange={flag("violations")} />Rule violations</label>
        <label className="mg-check"><input type="checkbox" checked={show.deadEnds} onChange={flag("deadEnds")} />Dead ends</label>
        <label className="mg-check"><input type="checkbox" checked={show.nodes} onChange={flag("nodes")} />Intersections</label>
        <label className="mg-check"><input type="checkbox" checked={show.footprint} onChange={flag("footprint")} />City footprint</label>
        <label className="mg-check"><input type="checkbox" checked={show.roads} disabled={!city.roads.length} onChange={flag("roads")} />Colour by road{!city.roads.length && <span className="muted"> (no roads yet)</span>}</label>
        <div className="mg-row">
          <label htmlFor="mg-block">Block size<output>{params.blockSize == null ? "auto" : `${params.blockSize} m`}</output></label>
          <input id="mg-block" type="range" min="40" max="250" step="5" value={params.blockSize ?? 70} onChange={num("blockSize")} />
          <button type="button" className="link-btn" onClick={() => set("blockSize", null)}>Use the default</button>
        </div>
        <h3>Hand-built test cities</h3>
        <div className="mg-presets">
          {DEMOS.map((d) => <button key={d.key} type="button" className={demo === d.key ? "on" : ""} title={d.note} onClick={() => pickDemo(d.key)}>{d.label}</button>)}
        </div>
      </details>
    </aside>

    <div className="mg-view">
      <div className="mg-stats">
        <div><b>{km(st.km.highway + st.km.primary + st.km.secondary + st.km.tertiary)}</b><span>of road</span></div>
        <div><b>{km(st.km.highway)}</b><span>highway</span></div>
        <div><b>{km(st.km.primary)}</b><span>primary</span></div>
        <div><b>{km(st.km.secondary)}</b><span>secondary</span></div>
        <div><b>{km(st.km.tertiary)}</b><span>tertiary</span></div>
        <div><b>{num2(st.edges)}</b><span>edges</span></div>
        <div><b>{num2(st.nodes.junctions)}</b><span>junctions</span></div>
        <div><b>{num2(st.nodes.deadEnds)}</b><span>dead ends</span></div>
        <div><b>{st.counts.roundabouts}</b><span>roundabouts</span></div>
        <div><b>{st.counts.signals}</b><span>signals</span></div>
        <div><b>{st.counts.bridges}</b><span>bridges</span></div>
        <div><b>{st.counts.railHighwayCrossings}</b><span>rail × highway</span></div>
        <div><b>{st.ms} ms</b><span>to generate</span></div>
        {info && <div><b>{info.primaries} / {info.secondaries}</b><span>primary / secondary roads</span></div>}
      </div>
      <div className="mg-tools">
        <button type="button" className="btn ghost" onClick={() => setResets((n) => n + 1)}>Reset view</button>
      </div>
      <div className={`mg-canvas-wrap ${stale ? "stale" : ""}`}>
        <MapCanvas city={city} show={show} fitKey={`${demo}-${city.meta.sizeKm}-${resets}`} focus={focus} />
      </div>
    </div>
  </div>;
}
