import { useCallback, useEffect, useRef, useState } from "react";
import avatarUrl from "../../assets/sprites/avatar.webp";
import pinUrl from "../../assets/sprites/pin-you.webp";
import { num } from "../../lib/format.js";
import { fmtDist, judge } from "../../lib/guess.js";
import { ALL_LAYERS, drawPlace, fitView } from "../../lib/placeDraw.js";

// GeoGuessr-style minimap drawn from the extracted road network (same renderer as the map explorer).
// Small in the corner, grows on hover, 3 sizes, a pin button keeps it open. Click to drop the pin, then Guess.
// You can't zoom out past the generated area and can't pan outside it.

const MAX_S = 8; // px per metre
const pinImg = new Image(); pinImg.src = pinUrl;
const faceImg = new Image(); faceImg.src = avatarUrl;
const ready = (i) => i.complete && i.naturalWidth > 0;

const clampView = (v, size, half) => {
  const s = Math.min(MAX_S, Math.max(fitView(half, size.w, size.h).s, v.s));
  const lim = (p, len) => (2 * half * s <= len ? len / 2 : Math.min(half * s, Math.max(len - half * s, p)));
  return { s, x: lim(v.x, size.w), y: lim(v.y, size.h) };
};

function fitPair(a, b, size, half) {
  const dx = Math.max(Math.abs(a.x - b.x), 60), dy = Math.max(Math.abs(a.y - b.y), 60);
  const s = Math.min(size.w / (dx * 1.9), size.h / (dy * 1.9));
  const cx = (a.x + b.x) / 2, cy = (a.y + b.y) / 2;
  return clampView({ s, x: size.w / 2 - cx * s, y: size.h / 2 + cy * s }, size, half);
}

function drawGuessPin(g, x, y) {
  const w = 34, h = 42.5;
  if (!ready(pinImg)) return;
  g.drawImage(pinImg, x - w / 2, y - h, w, h);
  if (!ready(faceImg)) return;
  const r = w * 0.3, cy = y - h + h * 0.4;
  g.save(); g.beginPath(); g.arc(x, cy, r, 0, Math.PI * 2); g.clip();
  g.drawImage(faceImg, x - r, cy - r, 2 * r, 2 * r);
  g.restore();
}
function drawTruthPin(g, x, y) {
  g.fillStyle = "#3fb97a"; g.strokeStyle = "#0d1117"; g.lineWidth = 2;
  g.beginPath(); g.moveTo(x, y);
  g.bezierCurveTo(x - 18, y - 16, x - 15, y - 40, x, y - 40);
  g.bezierCurveTo(x + 15, y - 40, x + 18, y - 16, x, y);
  g.fill(); g.stroke();
  g.fillStyle = "#fff"; g.beginPath(); g.arc(x, y - 26, 5, 0, Math.PI * 2); g.fill();
}

const Thumbtack = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <path d="M9 3h6l-1 6 3 3v2H7v-2l3-3-1-6z" /><path d="M12 14v7" /></svg>;

export default function Minimap({ place, truth, result, onGuess, onNext, busy }) {
  const half = place.meta.half_m;
  const wrap = useRef(null), cv = useRef(null), drag = useRef(null), touched = useRef(false), lastResult = useRef(null);
  const [size, setSize] = useState({ w: 240, h: 130 });
  const [view, setView] = useState({ s: 1, x: 0, y: 0 });
  const [pin, setPin] = useState(null);
  const [level, setLevel] = useState(0);
  const [lock, setLock] = useState(false);
  const [tick, setTick] = useState(0);
  const sizeRef = useRef(size), viewRef = useRef(view);
  sizeRef.current = size; viewRef.current = view;

  useEffect(() => { // sprites may still be loading on the first round
    const bump = () => setTick((t) => t + 1);
    [pinImg, faceImg].forEach((i) => { if (!i.complete) i.addEventListener("load", bump, { once: true }); });
  }, []);

  useEffect(() => {
    const ro = new ResizeObserver(([e]) => setSize({ w: Math.round(e.contentRect.width), h: Math.round(e.contentRect.height) }));
    ro.observe(wrap.current);
    return () => ro.disconnect();
  }, []);

  // Fit the whole area (or both pins after the guess) until the player moves the map themselves.
  useEffect(() => {
    if (lastResult.current !== result) { lastResult.current = result; touched.current = false; }
    setView((v) => {
      if (touched.current) return clampView(v, size, half);
      if (result) return fitPair(result.guess, truth, size, half);
      return clampView(fitView(half, size.w, size.h), size, half);
    });
  }, [size, result, truth, half]);

  useEffect(() => {
    const el = cv.current;
    const onWheel = (e) => {
      e.preventDefault();
      touched.current = true;
      const r = el.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top;
      setView((v) => {
        const s = v.s * Math.exp(-e.deltaY * 0.0015), f = Math.min(MAX_S, Math.max(0.001, s)) / v.s;
        return clampView({ s: v.s * f, x: mx - (mx - v.x) * f, y: my - (my - v.y) * f }, sizeRef.current, half);
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [half]);

  useEffect(() => {
    const c = cv.current, dpr = window.devicePixelRatio || 1;
    c.width = size.w * dpr; c.height = size.h * dpr;
    const g = c.getContext("2d");
    drawPlace(g, place, view, size, dpr, ALL_LAYERS);
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    const at = (p) => [view.x + p.x * view.s, view.y - p.y * view.s];
    if (result) {
      const [gx, gy] = at(result.guess), [tx, ty] = at(truth);
      g.strokeStyle = "#e6edf3"; g.lineWidth = 2; g.setLineDash([6, 6]);
      g.beginPath(); g.moveTo(gx, gy); g.lineTo(tx, ty); g.stroke(); g.setLineDash([]);
      drawTruthPin(g, tx, ty);
      drawGuessPin(g, gx, gy);
    } else if (pin) drawGuessPin(g, ...at(pin));
  }, [place, view, size, pin, result, truth, tick]);

  const down = (e) => { e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, moved: false }; };
  const move = (e) => {
    const d = drag.current;
    if (!d) return;
    if (!d.moved && Math.hypot(e.clientX - d.sx, e.clientY - d.sy) > 4) { d.moved = true; touched.current = true; }
    if (!d.moved) return;
    const dx = e.clientX - d.x, dy = e.clientY - d.y;
    d.x = e.clientX; d.y = e.clientY;
    setView((v) => clampView({ ...v, x: v.x + dx, y: v.y + dy }, sizeRef.current, half));
  };
  const up = (e) => {
    const d = drag.current;
    drag.current = null;
    if (!d || d.moved || result) return;
    const r = cv.current.getBoundingClientRect(), v = viewRef.current;
    const x = (e.clientX - r.left - v.x) / v.s, y = -(e.clientY - r.top - v.y) / v.s;
    if (Math.abs(x) <= half && Math.abs(y) <= half) setPin({ x, y });
  };

  const guess = () => onGuess({ guess: pin, ...judge(place.meta, truth, pin) });
  const tool = useCallback((label, onClick, content, extra = {}) =>
    <button type="button" className="mm-tool" aria-label={label} title={label} onClick={(e) => { onClick(); e.currentTarget.blur(); }} {...extra}>{content}</button>, []);

  return <div className={`mm lv${level} ${lock ? "lock" : ""} ${result ? "result" : ""}`}>
    <div className="mm-wrap" ref={wrap}>
      <canvas ref={cv} style={{ cursor: result ? "grab" : "crosshair" }} onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={() => { drag.current = null; }} role="img" aria-label="Minimap" />
    </div>
    {!result && <div className="mm-tools">
      {tool(lock ? "Let the map shrink again" : "Keep the map open", () => setLock((l) => !l), <Thumbtack />, { "aria-pressed": lock })}
      {tool("Make the map smaller", () => setLevel((l) => Math.max(0, l - 1)), "↘", { disabled: level === 0 })}
      {tool("Make the map larger", () => setLevel((l) => Math.min(2, l + 1)), "↖", { disabled: level === 2 })}
    </div>}
    <div className="mm-foot">
      {result ? <>
        <div className="mm-res">
          <b className={result.correct ? "ok" : ""}>{result.correct ? "Found it!" : `${fmtDist(result.dist)} away`}</b>
          <span>{result.correct ? `${fmtDist(result.dist)} off · ` : ""}{num(result.score)} points</span>
        </div>
        <button type="button" className="btn" onClick={onNext} disabled={busy}>{busy ? "Finding a place…" : "Next place"}</button>
      </> : <button type="button" className="btn mm-guess" disabled={!pin} onClick={guess}>Guess</button>}
    </div>
  </div>;
}
