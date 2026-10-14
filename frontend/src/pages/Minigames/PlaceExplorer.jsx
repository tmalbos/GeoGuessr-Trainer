import { useCallback, useEffect, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import { api, errorText } from "../../lib/api.js";
import { num } from "../../lib/format.js";
import { fetchPlace } from "../../lib/place.js";
import "./minigames.css";

// Press the button: the server picks a random country and a window of the chosen size that is guaranteed to be
// dense in roads (no trial and error), and this page draws it. World coordinates are metres, +y north.

const LAND = "#151b23", SEA = "#1a4562", LAKE = "#1f5578", RAIL = "#8b98a8";
// [colour, width in metres, minimum width in pixels], by road class 0..7 (track ... motorway)
const ROAD = [
  ["#5a4f40", 3, 0.7], ["#566170", 4, 0.8], ["#6f7f93", 5, 1], ["#8a99ac", 6, 1.2],
  ["#b4bfcc", 8, 1.6], ["#e0cf9a", 10, 2.2], ["#f0b46a", 12, 2.6], ["#ff7357", 14, 3],
];
const WATER_LINE = [["#2b6c93", 10, 1.4], ["#2b6c93", 6, 1.2], ["#2b6c93", 2, 0.8]]; // river, canal, stream

const fitView = (half, w, h) => ({ s: Math.min(w, h) / (half * 2 * 1.04), x: w / 2, y: h / 2 });
const niceLen = (s) => [10, 20, 50, 100, 200, 500, 1000, 2000, 5000].find((l) => l * s >= 70) ?? 5000;

function trace(g, L, ids, close) {
  const { start, xy } = L;
  g.beginPath();
  const n = ids ? ids.length : L.count;
  for (let k = 0; k < n; k++) {
    const i = ids ? ids[k] : k, a = start[i], b = start[i + 1];
    if (b - a < 2) continue;
    g.moveTo(xy[2 * a], xy[2 * a + 1]);
    for (let j = a + 1; j < b; j++) g.lineTo(xy[2 * j], xy[2 * j + 1]);
    if (close) g.closePath();
  }
}
function stroke(g, L, ids, color, widthM, minPx, s) {
  if (ids && !ids.length) return;
  trace(g, L, ids, false);
  g.strokeStyle = color; g.lineWidth = Math.max(minPx, widthM * s) / s; g.stroke();
}
function fill(g, L, color) {
  if (!L.count) return;
  trace(g, L, null, true);
  g.fillStyle = color; g.fill("evenodd");
}

function drawPlace(g, place, view, size, dpr, show) {
  const { layers: Ly, meta } = place, half = meta.half_m, s = view.s;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.fillStyle = "#0a0e13"; g.fillRect(0, 0, size.w, size.h);
  g.setTransform(dpr * s, 0, 0, -dpr * s, dpr * view.x, dpr * view.y);
  g.save();
  g.beginPath(); g.rect(-half, -half, 2 * half, 2 * half); g.clip();
  g.fillStyle = LAND; g.fillRect(-half, -half, 2 * half, 2 * half);
  g.lineCap = "round"; g.lineJoin = "round";
  if (show.sea) { fill(g, Ly.sea, SEA); fill(g, Ly.islands, LAND); }
  if (show.water) {
    fill(g, Ly.water_areas, LAKE);
    Ly.water_lines.groups.forEach((ids, k) => stroke(g, Ly.water_lines, ids, ...WATER_LINE[k], s));
  }
  if (show.rail && Ly.rail.count) {
    g.lineCap = "butt";
    stroke(g, Ly.rail, null, RAIL, 3.5, 2.2, s);
    trace(g, Ly.rail, null, false);
    g.strokeStyle = LAND; g.lineWidth = Math.max(1, 3.5 * s * 0.45) / s; g.setLineDash([9 / s, 9 / s]); g.stroke(); g.setLineDash([]);
    g.lineCap = "round";
  }
  if (show.roads) {
    const [tunnel, normal, bridge] = [Ly.roads.groups[2], Ly.roads.groups[0], Ly.roads.groups[1]];
    g.globalAlpha = 0.4;
    tunnel.forEach((ids, c) => stroke(g, Ly.roads, ids, ROAD[c][0], ROAD[c][1], ROAD[c][2], s));
    g.globalAlpha = 1;
    normal.forEach((ids, c) => stroke(g, Ly.roads, ids, ROAD[c][0], ROAD[c][1], ROAD[c][2], s));
    bridge.forEach((ids, c) => stroke(g, Ly.roads, ids, "#05080c", ROAD[c][1] + 3, ROAD[c][2] + 2.5, s));
    bridge.forEach((ids, c) => stroke(g, Ly.roads, ids, ROAD[c][0], ROAD[c][1], ROAD[c][2], s));
  }
  g.restore();

  // window frame and scale bar (screen space)
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  const x0 = view.x - half * s, y0 = view.y - half * s;
  g.strokeStyle = "#3a4656"; g.lineWidth = 1; g.strokeRect(x0, y0, 2 * half * s, 2 * half * s);
  const len = niceLen(s), px = len * s, bx = 16, by = size.h - 18;
  g.strokeStyle = "#e6edf3"; g.lineWidth = 2; g.lineCap = "butt";
  g.beginPath(); g.moveTo(bx, by - 5); g.lineTo(bx, by); g.lineTo(bx + px, by); g.lineTo(bx + px, by - 5); g.stroke();
  g.fillStyle = "#e6edf3"; g.font = "12px 'Bricolage Grotesque', system-ui, sans-serif";
  g.fillText(len >= 1000 ? `${len / 1000} km` : `${len} m`, bx, by - 9);
}

function MapCanvas({ place, show, fitKey, onDrawn }) {
  const wrap = useRef(null), cv = useRef(null), drag = useRef(null), reported = useRef(null);
  const [size, setSize] = useState({ w: 800, h: 600 });
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });
  const half = place.meta.half_m;

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);
  useEffect(() => { setView(fitView(half, size.w, size.h)); }, [fitKey, size.w, size.h]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const el = cv.current;
    const onWheel = (e) => {
      e.preventDefault();
      const r = el.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      setView((v) => {
        const s = Math.min(Math.max(v.s * Math.exp(-e.deltaY * 0.0015), 0.005), 40), f = s / v.s;
        return { s, x: mx - (mx - v.x) * f, y: my - (my - v.y) * f };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  useEffect(() => {
    const c = cv.current, dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const t = performance.now();
    drawPlace(c.getContext("2d"), place, view, size, dpr, show);
    if (reported.current !== place) { reported.current = place; onDrawn(Math.round(performance.now() - t)); }
  }, [place, view, size, show]); // eslint-disable-line react-hooks/exhaustive-deps

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
      <canvas ref={cv} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up} role="img" aria-label="Map window" />
    </div>
    <span className="mg-hint">Drag to pan · scroll to zoom</span>
  </>;
}

export default function PlaceExplorer() {
  const [info, setInfo] = useState(null);
  const [infoErr, setInfoErr] = useState("");
  const [si, setSi] = useState(2);
  const [place, setPlace] = useState(null);
  const [drawMs, setDrawMs] = useState(null);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState("");
  const [show, setShow] = useState({ sea: true, water: true, rail: true, roads: true });
  const [resets, setResets] = useState(0);
  const abort = useRef(null);
  const clickAt = useRef(0);
  const [total, setTotal] = useState(null);

  const loadInfo = useCallback(() => {
    setInfoErr("");
    return api("/minigames/countries").then(setInfo).catch((e) => setInfoErr(errorText(e)));
  }, []);
  useEffect(() => { loadInfo(); return () => abort.current?.abort(); }, [loadInfo]);

  const sizes = info?.sizes ?? [];
  const sizeKm = sizes[Math.min(si, sizes.length - 1)];
  const ready = info?.countries.filter((c) => c.valid[sizeKm] > 0).length ?? 0;

  const roll = async () => {
    abort.current?.abort();
    const ac = (abort.current = new AbortController());
    clickAt.current = performance.now();
    setLoading(true); setErr(""); setTotal(null);
    try {
      const p = await fetchPlace(sizeKm, null, ac.signal);
      setPlace(p); setDrawMs(null); setResets((n) => n + 1);
    } catch (e) {
      if (e.name !== "AbortError") setErr(errorText(e));
    } finally { if (abort.current === ac) setLoading(false); }
  };

  const onDrawn = useCallback((ms) => { setDrawMs(ms); setTotal(Math.round(performance.now() - clickAt.current)); }, []);
  const flag = (k) => (e) => setShow((s) => ({ ...s, [k]: e.target.checked }));

  if (infoErr) return <ErrorBox message={infoErr} onRetry={loadInfo} />;
  const L = place?.layers;
  const t = place?.meta.timing_ms;

  return <div className="mg">
    <aside className="mg-panel">
      <button type="button" className="btn mg-go" onClick={roll} disabled={loading || !ready}>{loading ? "Finding a place…" : "Random place"}</button>
      {info && !info.countries.length && <p className="muted mg-note">No maps are ready yet. Build them with <code>python -m scripts.build_minigame_gpkg</code>
        {info.pending.length > 0 && <> and index the existing ones with <code>python -m scripts.build_minigame_index</code></>}.</p>}
      {info && info.countries.length > 0 && <>
        <div className="mg-row">
          <label htmlFor="mg-size">Window size<output>{sizeKm} × {sizeKm} km</output></label>
          <input id="mg-size" type="range" min="0" max={sizes.length - 1} step="1" value={si} onChange={(e) => setSi(Number(e.target.value))} />
          <div className="mg-ends"><span>Small town</span><span>Big city</span></div>
        </div>
        <p className="muted mg-note">
          {ready ? `${ready} of ${info.countries.length} countries have a place this size.` : "No country has a place this big. Try a smaller window."}
          {info.pending.length > 0 && ` ${info.pending.length} more need indexing (python -m scripts.build_minigame_index).`}
        </p>
      </>}

      {place && <>
        <h3>This place</h3>
        <dl className="mg-facts">
          <dt>Country</dt><dd>{place.meta.country}</dd>
          <dt>Centre</dt><dd>{place.meta.lat.toFixed(4)}, {place.meta.lon.toFixed(4)}</dd>
          <dt>Roads</dt><dd>{num(L.roads.count)}</dd>
          <dt>Road points</dt><dd>{num(L.roads.pts)}</dd>
          <dt>Water shapes</dt><dd>{num(L.water_areas.count + L.water_lines.count + L.sea.count)}</dd>
          <dt>Download size</dt><dd>{(place.timing.bytes / 1e6).toFixed(2)} MB</dd>
        </dl>
        <h3>Speed</h3>
        <dl className="mg-facts">
          <dt>Pick a window</dt><dd>{t.pick} ms</dd>
          <dt>Read the file</dt><dd>{t.query} ms</dd>
          <dt>Whole request</dt><dd>{place.timing.fetch} ms</dd>
          <dt>Decode</dt><dd>{place.timing.decode} ms</dd>
          <dt>First draw</dt><dd>{drawMs == null ? "…" : `${drawMs} ms`}</dd>
          <dt>Click to map</dt><dd>{total == null ? "…" : `${total} ms`}</dd>
        </dl>
        <h3>Layers</h3>
        <label className="mg-check"><input type="checkbox" checked={show.roads} onChange={flag("roads")} />Roads</label>
        <label className="mg-check"><input type="checkbox" checked={show.rail} onChange={flag("rail")} />Railways</label>
        <label className="mg-check"><input type="checkbox" checked={show.water} onChange={flag("water")} />Lakes and rivers</label>
        <label className="mg-check"><input type="checkbox" checked={show.sea} onChange={flag("sea")} />Sea</label>
      </>}
    </aside>

    <div className="mg-view">
      {err && <ErrorBox message={err} onRetry={roll} />}
      <div className="mg-tools">
        <button type="button" className="btn ghost" onClick={() => setResets((n) => n + 1)} disabled={!place}>Reset view</button>
      </div>
      <div className={`mg-canvas-wrap ${loading ? "busy" : ""}`}>
        {place ? <MapCanvas place={place} show={show} fitKey={resets} onDrawn={onDrawn} />
          : <div className="mg-empty">Press “Random place” to see a map.</div>}
      </div>
    </div>
  </div>;
}
