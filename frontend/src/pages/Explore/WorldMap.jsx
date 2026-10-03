import { memo, useEffect, useRef, useState } from "react";
import { clueCountriesCached, loadClueCountries } from "./clueCountries.js";
import { loadWorld } from "./geo.js";
import "./worldmap.css";

const MAX_K = 120; // zoom relative to "whole world fits the tab"
// ── Click circles for tiny countries: tweak these three to taste (all in screen pixels) ──
const DOT_PX = 3; // radius of the VISIBLE circle
const DOT_HIT_PX = 6; // radius of the invisible click area around it (keep >= DOT_PX)
const DOT_SHOW_PX = 5; // a circle disappears once its country's biggest piece is wider than this on screen
const STROKE_PX = 0.6; // border width in screen pixels
const DRAG_PX = 4; // movement before a press counts as a drag instead of a click
const SMOOTH_MS = 60; // zoom easing time constant

// Where the map was left, so coming back from a country page keeps the view.
let saved = null;

// Static geometry: React never re-renders this except when `has` (countries with clues) arrives.
// All zooming happens by editing the <g> transform directly.
const Shapes = memo(function Shapes({ world, has }) {
  return <>
    <path className="wm-land" d={world.land} fillRule="evenodd" />
    {world.countries.map((c) => {
      const none = has && !has.has(String(c.id).toUpperCase());
      return <g key={c.id} className={`wm-c${none ? " none" : ""}`} data-id={c.id} role="button"
        aria-label={none ? `${c.name}, no clues yet` : c.name}>
        <path d={c.d} fillRule="evenodd" />
        {c.dots.map(([x, y], i) => <g key={i} className="wm-pt" data-size={c.size}>
          <circle className="wm-hit" cx={x} cy={y} r="0" />
          <circle className="wm-dot" cx={x} cy={y} r="0" />
        </g>)}
      </g>;
    })}
  </>;
});

export default function WorldMap({ onSelect }) {
  const [world, setWorld] = useState(null);
  // Set of ISO codes with clues. Comes from an in-memory cache, so after the first visit it is known immediately.
  const [has, setHas] = useState(clueCountriesCached);
  const [hasFailed, setHasFailed] = useState(false); // couldn't load it: show the map without dimming
  const [err, setErr] = useState("");
  const box = useRef(null);
  const gRef = useRef(null);
  const api = useRef({ zoomBy: () => {} });
  const selectRef = useRef(onSelect);
  selectRef.current = onSelect;

  // The map stays hidden until it knows which countries have clues, so it never flashes "all colored".
  const known = !!has || hasFailed;

  useEffect(() => {
    let off = false;
    loadWorld().then((w) => !off && setWorld(w)).catch((e) => !off && setErr(e.message));
    if (!has) loadClueCountries().then((s) => !off && setHas(s)).catch(() => !off && setHasFailed(true));
    return () => { off = true; };
  }, []);

  useEffect(() => {
    if (!world) return undefined;
    const el = box.current;
    const g = gRef.current;
    const unit = world.unit;
    // v = what is drawn, t = where it is heading. Wheel/buttons move t and v eases toward it;
    // dragging and pinching move both at once so they feel attached to the finger.
    const s = { W: 1, H: 1, base: 1, v: { k: 1, x: 0, y: 0 }, t: { k: 1, x: 0, y: 0 }, raf: 0, last: 0 };
    const dots = [...g.querySelectorAll(".wm-pt")].map((d) => ({ el: d, hit: d.firstChild, dot: d.lastChild, size: Number(d.dataset.size), on: null }));

    const clampView = (v) => {
      const mw = 360 * s.base * v.k;
      const mh = 180 * s.base * v.k;
      v.x = mw <= s.W ? (s.W - mw) / 2 : Math.min(0, Math.max(s.W - mw, v.x));
      v.y = mh <= s.H ? (s.H - mh) / 2 : Math.min(0, Math.max(s.H - mh, v.y));
    };

    const apply = () => {
      const { k, x, y } = s.v;
      const scale = (s.base * k) / unit;
      g.setAttribute("transform", `translate(${x} ${y}) scale(${scale})`);
      g.setAttribute("stroke-width", STROKE_PX / scale);
      const r = DOT_PX / scale;
      const rHit = Math.max(DOT_HIT_PX, DOT_PX) / scale;
      for (const d of dots) {
        const show = d.size * s.base * k < DOT_SHOW_PX;
        if (show !== d.on) { d.on = show; d.el.style.display = show ? "" : "none"; }
        if (show) { d.dot.setAttribute("r", r); d.hit.setAttribute("r", rHit); }
      }
    };

    const tick = (now) => {
      const dt = Math.min(64, now - (s.last || now));
      s.last = now;
      const a = 1 - Math.exp(-dt / SMOOTH_MS);
      const { v, t } = s;
      v.k += (t.k - v.k) * a;
      v.x += (t.x - v.x) * a;
      v.y += (t.y - v.y) * a;
      const done = Math.abs(t.k - v.k) < t.k * 0.0005 && Math.abs(t.x - v.x) < 0.2 && Math.abs(t.y - v.y) < 0.2;
      if (done) Object.assign(v, t);
      apply();
      s.raf = done ? 0 : requestAnimationFrame(tick);
      if (done) s.last = 0;
    };
    const kick = () => { if (!s.raf) { s.last = 0; s.raf = requestAnimationFrame(tick); } };

    // Zoom the target by `factor`, keeping the map point under (px, py) where it is.
    const zoomAt = (px, py, factor) => {
      const t = s.t;
      const k = Math.min(MAX_K, Math.max(1, t.k * factor));
      const ux = (px - t.x) / t.k;
      const uy = (py - t.y) / t.k;
      t.k = k; t.x = px - ux * k; t.y = py - uy * k;
      clampView(t);
      kick();
    };
    api.current.zoomBy = (f) => zoomAt(s.W / 2, s.H / 2, f);

    const measure = () => {
      const r = el.getBoundingClientRect();
      s.W = r.width; s.H = r.height;
      s.base = Math.min(s.W / 360, s.H / 180);
    };
    measure();
    if (saved) Object.assign(s.t, saved);
    clampView(s.t);
    Object.assign(s.v, s.t);
    apply();
    const ro = new ResizeObserver(() => { measure(); clampView(s.t); Object.assign(s.v, s.t); apply(); });
    ro.observe(el);

    const local = (e) => { const r = el.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };

    const onWheel = (e) => {
      e.preventDefault();
      const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
      const [px, py] = local(e);
      zoomAt(px, py, Math.exp(-dy * (e.ctrlKey ? 0.01 : 0.002)));
    };

    const ptrs = new Map();
    let drag = null;
    let pinch = null;
    const mid = () => {
      const [a, b] = [...ptrs.values()];
      const r = el.getBoundingClientRect();
      return { dist: Math.hypot(a.x - b.x, a.y - b.y), mx: (a.x + b.x) / 2 - r.left, my: (a.y + b.y) / 2 - r.top };
    };

    const onDown = (e) => {
      if (e.target.closest(".wm-zoom")) return;
      if (e.pointerType === "mouse" && e.button !== 0) return;
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (ptrs.size === 1) {
        drag = { id: e.pointerId, x: e.clientX, y: e.clientY, vx: s.v.x, vy: s.v.y, moved: false, hit: e.target.closest(".wm-c")?.dataset.id ?? null };
      } else if (ptrs.size === 2) {
        if (drag) drag.moved = true; // a two-finger gesture never counts as a click
        pinch = mid();
      }
    };

    const onMove = (e) => {
      if (!ptrs.has(e.pointerId)) return;
      ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (ptrs.size >= 2 && pinch) {
        const m = mid();
        const { v, t } = s;
        const k = Math.min(MAX_K, Math.max(1, v.k * (m.dist / pinch.dist)));
        const ux = (pinch.mx - v.x) / v.k;
        const uy = (pinch.my - v.y) / v.k;
        t.k = k; t.x = m.mx - ux * k; t.y = m.my - uy * k;
        clampView(t);
        Object.assign(v, t);
        pinch = m;
        apply();
        return;
      }
      if (!drag || e.pointerId !== drag.id) return;
      const dx = e.clientX - drag.x;
      const dy = e.clientY - drag.y;
      if (!drag.moved) {
        if (Math.hypot(dx, dy) < DRAG_PX) return;
        drag.moved = true;
        el.setPointerCapture(e.pointerId);
        el.classList.add("moving");
      }
      const { v, t } = s;
      v.x = drag.vx + dx; v.y = drag.vy + dy;
      clampView(v);
      Object.assign(t, v);
      apply();
    };

    const end = (e, cancelled) => {
      if (!ptrs.delete(e.pointerId)) return;
      if (ptrs.size < 2) pinch = null;
      if (drag && e.pointerId === drag.id) {
        const hit = !drag.moved && !cancelled ? drag.hit : null;
        drag = null;
        el.classList.remove("moving");
        if (hit) selectRef.current?.(hit);
      }
    };
    const onUp = (e) => end(e, false);
    const onCancel = (e) => end(e, true);

    const onKey = (e) => {
      const step = 80;
      const pan = { ArrowLeft: [step, 0], ArrowRight: [-step, 0], ArrowUp: [0, step], ArrowDown: [0, -step] }[e.key];
      if (pan) { e.preventDefault(); s.t.x += pan[0]; s.t.y += pan[1]; clampView(s.t); kick(); }
      else if (e.key === "+" || e.key === "=") zoomAt(s.W / 2, s.H / 2, 1.5);
      else if (e.key === "-" || e.key === "_") zoomAt(s.W / 2, s.H / 2, 1 / 1.5);
    };

    el.addEventListener("wheel", onWheel, { passive: false });
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerup", onUp);
    el.addEventListener("pointercancel", onCancel);
    el.addEventListener("keydown", onKey);
    return () => {
      saved = { ...s.t };
      cancelAnimationFrame(s.raf);
      ro.disconnect();
      el.removeEventListener("wheel", onWheel);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerup", onUp);
      el.removeEventListener("pointercancel", onCancel);
      el.removeEventListener("keydown", onKey);
    };
  }, [world]);

  return <div className="wm" ref={box} tabIndex={0} aria-label="World map. Click a country to open its clues. Use plus and minus to zoom, arrow keys to move.">
    {world && <svg className="wm-svg" width="100%" height="100%" style={known ? undefined : { visibility: "hidden" }}>
      <g ref={gRef}><Shapes world={world} has={has} /></g>
    </svg>}
    {(!world || !known) && !err && <p className="wm-msg muted">Loading map…</p>}
    {err && <p className="wm-msg err">{err}</p>}
    {world && <div className="wm-zoom">
      <button type="button" className="btn" aria-label="Zoom in" onClick={() => api.current.zoomBy(1.6)}>+</button>
      <button type="button" className="btn" aria-label="Zoom out" onClick={() => api.current.zoomBy(1 / 1.6)}>−</button>
    </div>}
  </div>;
}
