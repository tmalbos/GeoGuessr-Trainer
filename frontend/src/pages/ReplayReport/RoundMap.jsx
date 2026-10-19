import { useEffect, useRef, useState } from "react";
import { tileUrl } from "../../lib/googleTiles.js";

// A small map of our own: Google's roadmap tiles, drag to pan, wheel / buttons to zoom (fractional zoom, like Leaflet's
// zoomSnap 0). Tiles and everything drawn on top (path, pins, searched area, highlight) live under ONE view state and are
// moved in the same step, so they can never drift apart.

let seq = 0;

const TILE = 256;
const MIN_ZOOM = 2;
const MAX_ZOOM = 21;
const SINGLE_POINT_ZOOM = 15;
const PATH = "#f2c14e";
const HILITE = "#3fb97a"; // selection green (same as --ok)
const SETTLE_MS = 120; // after the zoom stops, redraw the overlay at the exact zoom

// ── Web Mercator in world fractions (0..1 across the whole world) ──
const RAD = Math.PI / 180;
const fx = (lng) => (lng + 180) / 360;
const fy = (lat) => {
  const s = Math.sin(Math.max(-85.0511, Math.min(85.0511, lat)) * RAD);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const f1 = (n) => n.toFixed(1);

const boundsOf = (pts) => {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  for (const [lat, lng] of pts) { w = Math.min(w, lng); e = Math.max(e, lng); s = Math.min(s, lat); n = Math.max(n, lat); }
  return { w, s, e, n };
};

// The player's pin: a round avatar with a white ring and a small tail (tip = the guessed spot).
const guessPin = (id) => `<svg viewBox="0 0 40 50" width="36" height="45" aria-hidden="true">
  <defs><clipPath id="${id}"><circle cx="20" cy="20" r="16"/></clipPath></defs>
  <path d="M20 49L12 35H28Z" fill="#fff"/>
  <circle cx="20" cy="20" r="19" fill="#fff"/>
  <circle cx="20" cy="20" r="16" fill="#f2b880"/>
  <g clip-path="url(#${id})"><circle cx="20" cy="16" r="6.5" fill="#6f4a2f"/><ellipse cx="20" cy="36" rx="12" ry="9.5" fill="#6f4a2f"/></g>
</svg>`;

// The real spot: GeoGuessr's own flag pin (frontend/public/pins/correct-location.webp), ringed in white by CSS.
const REAL_PIN = '<img src="/pins/correct-location.webp" alt="" width="32" height="32" />';

// ── Drawing the overlay. Coordinates are px at zoom `z`, relative to (ox, oy). ──
const NS = 'vector-effect="non-scaling-stroke"'; // strokes keep their width while the overlay is scaled mid-zoom
function overlayHtml(data, focus, z, ox, oy) {
  const { real, guess, path, searched, id } = data;
  const S = TILE * 2 ** z;
  const P = (lat, lng) => [fx(lng) * S - ox, fy(lat) * S - oy];
  const line = (pts) => pts.map(([lat, lng], i) => { const [x, y] = P(lat, lng); return `${i ? "L" : "M"}${f1(x)} ${f1(y)}`; }).join("");

  let stat = "";
  const areas = searched.map((rings) => rings.map((r) => `${line(r)}Z`).join("")).join("");
  if (areas) stat += `<path d="${areas}" fill="#2d7ea3" fill-opacity=".2" stroke="#6cc0e5" stroke-width="1" fill-rule="evenodd" ${NS}/>`;
  const segs = path.segments.map(line).join("");
  if (segs) stat += `<path d="${segs}" fill="none" stroke="${PATH}" stroke-width="3" stroke-opacity=".9" stroke-linejoin="round" stroke-linecap="round" ${NS}/>`;
  const jumps = path.jumps.map(line).join("");
  if (jumps) stat += `<path d="${jumps}" fill="none" stroke="${PATH}" stroke-width="2" stroke-opacity=".6" stroke-dasharray="4 6" ${NS}/>`;
  if (guess) {
    // Dotted gray line between the pins, like GeoGuessr's result map.
    stat += `<path d="${line([[real.lat, real.lng], [guess.lat, guess.lng]])}" fill="none" stroke="#5f6368" stroke-width="3" stroke-opacity=".95" stroke-dasharray="0.1 8" stroke-linecap="round" ${NS}/>`;
  }

  // The highlight of the selected event.
  let hl = "";
  const pts = focus?.points ?? [];
  if (pts.length && !focus.path) {
    for (const [lat, lng] of pts) {
      const [x, y] = P(lat, lng);
      hl += `<circle cx="${f1(x)}" cy="${f1(y)}" r="9" fill="${HILITE}" fill-opacity=".9" stroke="#fff" stroke-width="3" ${NS}/>`;
    }
  } else if (pts.length) {
    hl += `<path d="${line(pts)}" fill="none" stroke="${HILITE}" stroke-width="5" stroke-opacity=".95" stroke-linejoin="round" stroke-linecap="round" ${NS}/>`;
    const [sx, sy] = P(pts[0][0], pts[0][1]);
    hl += `<circle cx="${f1(sx)}" cy="${f1(sy)}" r="6" fill="#fff" stroke="${HILITE}" stroke-width="3" ${NS}/>`;
    // Arrowhead at the end, pointing the way the run went on screen (from the last point that is at least 4 px back).
    const last = pts[pts.length - 1];
    const [tx, ty] = P(last[0], last[1]);
    const from = pts.slice(0, -1).reverse().map(([lat, lng]) => P(lat, lng)).find(([x, y]) => Math.hypot(tx - x, ty - y) >= 4);
    if (from) {
      const a = (Math.atan2(ty - from[1], tx - from[0]) * 180) / Math.PI;
      hl += `<g transform="translate(${f1(tx)} ${f1(ty)}) rotate(${a.toFixed(1)}) scale(1.1667) translate(-12 -12)"><path d="M3 4L22 12L3 20L8 12Z" fill="${HILITE}" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></g>`;
    }
  }

  // Pins keep their size while the overlay is scaled mid-zoom: they are scaled back by --ik around their tip.
  const pin = (cls, x, y, w, h, ax, ay, zi, inner) =>
    `<div class="rr-pin ${cls}" style="left:${f1(x - ax)}px;top:${f1(y - ay)}px;width:${w}px;height:${h}px;z-index:${zi};transform-origin:${ax}px ${ay}px;transform:scale(var(--ik,1))">${inner}</div>`;
  let pins = "";
  if (guess) { const [x, y] = P(guess.lat, guess.lng); pins += pin("", x, y, 36, 45, 18, 44, 400, guessPin(id)); }
  const [rx, ry] = P(real.lat, real.lng);
  pins += pin("rr-real", rx, ry, 36, 36, 18, 18, 500, REAL_PIN);

  return `<svg class="rr-svg">${stat}${hl}</svg>${pins}`;
}

/** The map engine. `host` = { box, tiles, overlay } elements. */
export function createEngine({ box, tiles, overlay }) {
  const v = { z: MIN_ZOOM, cx: 0.5, cy: 0.5 }; // view: zoom (fractional) and center (world fractions)
  const size = { w: 0, h: 0 };
  const layers = new Map(); // tile zoom -> { el, ox, oy, imgs, pending }
  const ov = { z: null, ox: 0, oy: 0, dirty: true };
  let data = null;
  let focus = null;
  let tileZoom = 0;
  let timer = 0;
  let relayout = null;

  // ── Tiles: one layer per whole zoom; the previous one stays under the new one until it has loaded ──
  const prune = () => {
    const cur = layers.get(tileZoom);
    if (!cur || cur.pending > 0) return;
    for (const [z, L] of layers) if (z !== tileZoom) { L.el.remove(); layers.delete(z); }
  };

  const syncTiles = () => {
    tileZoom = clamp(Math.round(v.z), 0, MAX_ZOOM);
    let L = layers.get(tileZoom);
    if (!L) {
      const S = TILE * 2 ** tileZoom;
      const el = document.createElement("div");
      el.className = "rr-tl";
      tiles.appendChild(el);
      L = { el, ox: Math.floor((v.cx * S) / TILE) * TILE, oy: Math.floor((v.cy * S) / TILE) * TILE, imgs: new Map(), pending: 0 };
      layers.set(tileZoom, L);
      // Safety net: never keep an old zoom layer around because some tile never answered.
      const mine = tileZoom;
      setTimeout(() => { const M = layers.get(mine); if (M) { M.pending = 0; prune(); } }, 2500);
    }
    const n = 2 ** tileZoom;
    const S = TILE * n;
    const k = 2 ** (v.z - tileZoom);
    const hw = size.w / 2 / k;
    const hh = size.h / 2 / k;
    const x0 = Math.floor((v.cx * S - hw) / TILE) - 1;
    const x1 = Math.floor((v.cx * S + hw) / TILE) + 1;
    const y0 = Math.max(0, Math.floor((v.cy * S - hh) / TILE) - 1);
    const y1 = Math.min(n - 1, Math.floor((v.cy * S + hh) / TILE) + 1);
    for (let x = x0; x <= x1; x++) {
      for (let y = y0; y <= y1; y++) {
        const key = `${x},${y}`;
        if (L.imgs.has(key)) continue;
        const img = document.createElement("img");
        img.className = "rr-tile";
        img.alt = "";
        img.draggable = false;
        img.style.left = `${x * TILE - L.ox}px`;
        img.style.top = `${y * TILE - L.oy}px`;
        L.pending++;
        const done = () => { img.onload = img.onerror = null; L.pending--; prune(); };
        img.onload = done;
        img.onerror = done;
        img.src = tileUrl(x, y, tileZoom);
        L.el.appendChild(img);
        L.imgs.set(key, img);
      }
    }
    for (const [key, img] of L.imgs) { // drop tiles far from the view
      const [x, y] = key.split(",").map(Number);
      if (x < x0 - 2 || x > x1 + 2 || y < y0 - 2 || y > y1 + 2) {
        if (img.onload) L.pending--;
        img.onload = img.onerror = null;
        img.removeAttribute("src");
        img.remove();
        L.imgs.delete(key);
      }
    }
    // Every layer follows the view, so the old one stays in place while the new one loads.
    for (const [z, M] of layers) {
      const kk = 2 ** (v.z - z);
      const SS = TILE * 2 ** z;
      M.el.style.transform = `translate(${size.w / 2 - (v.cx * SS - M.ox) * kk}px,${size.h / 2 - (v.cy * SS - M.oy) * kk}px) scale(${kk})`;
    }
  };

  // ── Overlay ──
  const redraw = () => {
    if (!data) return;
    ov.z = v.z;
    ov.ox = v.cx * TILE * 2 ** v.z;
    ov.oy = v.cy * TILE * 2 ** v.z;
    overlay.innerHTML = overlayHtml(data, focus, ov.z, ov.ox, ov.oy);
    ov.dirty = false;
  };

  const placeOverlay = (settleNow) => {
    if (!data) return;
    if (ov.dirty || ov.z === null || settleNow) redraw();
    else if (Math.abs(v.z - ov.z) > 1e-6) { // zooming: scale what is drawn, redraw at the exact zoom once it settles
      clearTimeout(timer);
      timer = setTimeout(() => { redraw(); render(); }, SETTLE_MS);
    }
    const k = 2 ** (v.z - ov.z);
    const S = TILE * 2 ** ov.z;
    overlay.style.setProperty("--ik", String(1 / k));
    overlay.style.transform = `translate(${size.w / 2 - (v.cx * S - ov.ox) * k}px,${size.h / 2 - (v.cy * S - ov.oy) * k}px) scale(${k})`;
  };

  function render(settleNow = false) {
    size.w = box.clientWidth;
    size.h = box.clientHeight;
    if (!size.w || !size.h) return;
    v.cy = clamp(v.cy, 0, 1);
    syncTiles();
    placeOverlay(settleNow);
  }

  // ── View control ──
  const fit = (b, pad, maxZoom) => {
    const W = Math.max(box.clientWidth - 2 * pad, 1);
    const H = Math.max(box.clientHeight - 2 * pad, 1);
    const dx = Math.max((b.e - b.w) / 360, 1e-12);
    const dy = Math.max(fy(b.s) - fy(b.n), 1e-12);
    v.z = clamp(Math.log2(Math.min(W / (dx * TILE), H / (dy * TILE))), MIN_ZOOM, maxZoom);
    v.cx = (fx(b.w) + fx(b.e)) / 2;
    v.cy = (fy(b.s) + fy(b.n)) / 2;
  };
  const setView = (lat, lng, z) => { v.z = clamp(z, MIN_ZOOM, MAX_ZOOM); v.cx = fx(lng); v.cy = fy(lat); };
  const zoomAt = (px, py, dz) => {
    const z2 = clamp(v.z + dz, MIN_ZOOM, MAX_ZOOM);
    const S1 = TILE * 2 ** v.z;
    const S2 = TILE * 2 ** z2;
    v.cx += (px - size.w / 2) / S1 - (px - size.w / 2) / S2;
    v.cy += (py - size.h / 2) / S1 - (py - size.h / 2) / S2;
    v.z = z2;
    render();
  };

  // ── Input: drag to pan, wheel to zoom ──
  let drag = null;
  const onDown = (e) => {
    if (e.target.closest?.(".rr-zoom")) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY };
    box.setPointerCapture?.(e.pointerId);
    box.classList.add("moving");
  };
  const onMove = (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const S = TILE * 2 ** v.z;
    v.cx -= (e.clientX - drag.x) / S;
    v.cy -= (e.clientY - drag.y) / S;
    drag.x = e.clientX;
    drag.y = e.clientY;
    render();
  };
  const onUp = (e) => {
    if (drag && e.pointerId === drag.id) { drag = null; box.classList.remove("moving"); }
  };
  const onWheel = (e) => {
    e.preventDefault();
    const r = box.getBoundingClientRect();
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
    zoomAt(e.clientX - r.left, e.clientY - r.top, -dy * (e.ctrlKey ? 0.01 : 0.002) / Math.LN2);
  };
  box.addEventListener("pointerdown", onDown);
  box.addEventListener("pointermove", onMove);
  box.addEventListener("pointerup", onUp);
  box.addEventListener("pointercancel", onUp);
  box.addEventListener("wheel", onWheel, { passive: false });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (relayout) relayout(); else render(); }) : null;
  ro?.observe(box);

  return {
    view: v,
    render,
    fit,
    setView,
    zoomBy: (dz) => zoomAt(size.w / 2, size.h / 2, dz),
    setData(d) { data = d; ov.dirty = true; },
    setFocus(f) { focus = f; ov.dirty = true; },
    onResize(fn) { relayout = fn; },
    tileCount: () => [...layers.values()].reduce((a, L) => a + L.imgs.size, 0),
    destroy() {
      clearTimeout(timer);
      ro?.disconnect();
      box.removeEventListener("pointerdown", onDown);
      box.removeEventListener("pointermove", onMove);
      box.removeEventListener("pointerup", onUp);
      box.removeEventListener("pointercancel", onUp);
      box.removeEventListener("wheel", onWheel);
      for (const L of layers.values()) for (const img of L.imgs.values()) { img.onload = img.onerror = null; img.removeAttribute("src"); }
    },
  };
}

/** `focus` (from the clicked event) is one of:
 *    { bbox: [west, south, east, north] }  exactly that area (what the player's minimap showed)
 *    { points: [[lat, lng], ...], path? }  highlight those points. A path (the steps of a run) is drawn as an
 *                                          arrow from where it started to where it ended, and the view fits the
 *                                          whole path walked in the round so the arrow is seen in context.
 *  null = the default view (real spot + guess). The map has the same aspect ratio as the minimap
 *  window used for the "seen" calculations (MAP_PX in replay_behavior.py), so a bbox fits it exactly. */
export default function RoundMap({ real, guess, path, searched, focus }) {
  const box = useRef(null);
  const tiles = useRef(null);
  const overlay = useRef(null);
  const [eng, setEng] = useState(null);
  const [err, setErr] = useState("");
  const avatarId = useRef(`rr-av-${++seq}`);

  useEffect(() => {
    const e = createEngine({ box: box.current, tiles: tiles.current, overlay: overlay.current });
    setEng(e);
    return () => e.destroy();
  }, []);

  useEffect(() => {
    if (eng) eng.setData({ real, guess, path, searched, id: avatarId.current });
  }, [eng, real, guess, path, searched]);

  // The selected event: where the view goes and what is highlighted.
  useEffect(() => {
    if (!eng) return undefined;
    const apply = () => {
      try {
        if (!box.current.clientWidth) return;
        const pts = focus?.points ?? [];
        if (!focus || (!focus.bbox && !pts.length)) {
          const all = [[real.lat, real.lng]];
          if (guess) all.push([guess.lat, guess.lng]);
          if (all.length === 1) eng.setView(real.lat, real.lng, 18); else eng.fit(boundsOf(all), 56, 18);
        } else if (focus.bbox) {
          const [w, s, e, n] = focus.bbox;
          eng.fit({ w, s, e, n }, 0, MAX_ZOOM);
        } else if (focus.path) {
          eng.fit(boundsOf([...path.segments.flat(), ...path.jumps.flat(), ...pts]), 40, 19); // everything walked in the round
        } else if (pts.length === 1) {
          eng.setView(pts[0][0], pts[0][1], SINGLE_POINT_ZOOM);
        } else {
          eng.fit(boundsOf(pts), 48, 19);
        }
        eng.setFocus(focus);
        eng.render(true);
      } catch (e) { console.error("RoundMap:", e); setErr(`Map error: ${e?.message ?? e}`); }
    };
    eng.onResize(apply); // the map is sized by CSS (fixed ratio): refit whenever its size changes
    apply();
    return () => eng.onResize(null);
  }, [eng, focus, real, guess, path, searched]);

  return <>
    <div ref={box} className="rr-map" role="img" aria-label="Map with the actual location, your guess and your path">
      <div ref={tiles} className="rr-tiles" />
      <div ref={overlay} className="rr-layer" />
      <div className="rr-zoom">
        <button type="button" className="btn" aria-label="Zoom in" onClick={() => eng?.zoomBy(1)}>+</button>
        <button type="button" className="btn" aria-label="Zoom out" onClick={() => eng?.zoomBy(-1)}>−</button>
      </div>
      <div className="rr-attr">Map data © Google</div>
    </div>
    {err && <p className="err" style={{ margin: "8px 0 0" }}>{err}</p>}
  </>;
}
