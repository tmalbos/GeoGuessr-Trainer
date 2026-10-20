// Shared map engine: Google roadmap tiles + a view state (zoom, center) that drives everything.
// Used by the replay report map (RoundMap.jsx) and the polygonizer map (PolyMap.jsx).
//
// SYNC: tiles and the caller's overlay are both updated from the SAME view state inside the SAME render() call,
// and animations are a requestAnimationFrame loop that only changes that view state. There is nothing animated
// by CSS, so tiles and overlay can't drift apart. The overlay is plugged in as `layer(view, size, settled)`:
// it runs on every frame; `settled` is true when the view stopped moving (a good moment to redraw precisely).
//
// ZOOM: wheel and +/- move exactly one whole level (2x). Programmatic fits may land on a fractional zoom
// (so a bbox fits exactly); the next step snaps to the next whole level.

import { tileUrl } from "./googleTiles.js";

export const TILE = 256;
export const MAX_ZOOM = 21;
const MIN_ZOOM = 1;
const ZOOM_MS = 450;
const FLY_MS = 450;
const WHEEL_STEP = 60; // accumulated wheel delta that counts as one tick
const WHEEL_COOLDOWN_MS = 140; // at most one zoom level per this time (trackpads fire many events)

const RAD = Math.PI / 180;
export const fx = (lng) => (lng + 180) / 360;
export const fy = (lat) => {
  const s = Math.sin(Math.max(-85.0511, Math.min(85.0511, lat)) * RAD);
  return 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI);
};
export const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const ease = (t) => 1 - (1 - t) ** 3;

/** host = { box, tiles, layer?, tileClass?, layerClass? }. `box` gets the "moving" class while dragging. */
export function createMapEngine({ box, tiles, layer, tileClass = "map-tile", layerClass = "map-tl" }) {
  const v = { z: 2, cx: 0.5, cy: 0.5 };
  const size = { w: 0, h: 0 };
  const layers = new Map(); // tile zoom -> { el, ox, oy, imgs, pending }
  let tileZoom = 0;
  let tilesOn = true;
  let anim = null;
  let raf = 0;
  let relayout = null;

  const measure = () => {
    size.w = box.clientWidth;
    size.h = box.clientHeight;
    return size.w > 0 && size.h > 0;
  };
  // The only place that decides what views are allowed.
  const limit = (z, cx, cy) => ({ z: clamp(z, MIN_ZOOM, MAX_ZOOM), cx, cy: clamp(cy, 0, 1) });

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
      el.className = layerClass;
      tiles.appendChild(el);
      L = { el, ox: Math.floor((v.cx * S) / TILE) * TILE, oy: Math.floor((v.cy * S) / TILE) * TILE, imgs: new Map(), pending: 0 };
      layers.set(tileZoom, L);
      const mine = tileZoom; // safety net: never keep an old layer because some tile never answered
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
        img.className = tileClass;
        img.alt = "";
        img.draggable = false;
        // 1 px bigger than the tile: neighbours overlap, so no hairline seams show between tiles.
        img.style.width = img.style.height = `${TILE + 1}px`;
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
    for (const [z, M] of layers) { // every layer follows the view
      const kk = 2 ** (v.z - z);
      const SS = TILE * 2 ** z;
      M.el.style.transform = `translate(${size.w / 2 - (v.cx * SS - M.ox) * kk}px,${size.h / 2 - (v.cy * SS - M.oy) * kk}px) scale(${kk})`;
    }
  };

  function render(settled = false) {
    if (!measure()) return;
    Object.assign(v, limit(v.z, v.cx, v.cy));
    if (tilesOn) syncTiles();
    layer?.(v, size, settled);
  }

  // ── Animation: only ever changes the view state, then renders everything from it ──
  const stop = () => { cancelAnimationFrame(raf); raf = 0; anim = null; };
  const start = (a) => { stop(); anim = { ...a, t0: performance.now() }; raf = requestAnimationFrame(step); };

  function step() {
    raf = 0;
    if (!anim) return;
    // performance.now() on both ends (the rAF timestamp can be OLDER than t0, which made t negative and the
    // easing push the view the wrong way for the first frame). Clamped to 0..1 as well.
    const t = clamp((performance.now() - anim.t0) / anim.dur, 0, 1);
    const e = ease(t);
    v.z = anim.z0 + (anim.z1 - anim.z0) * e;
    if (anim.anchor) { // zoom around a point: keep that spot under the cursor
      const S = TILE * 2 ** v.z;
      v.cx = anim.anchor.fx - (anim.anchor.px - size.w / 2) / S;
      v.cy = anim.anchor.fy - (anim.anchor.py - size.h / 2) / S;
    } else {
      v.cx = anim.cx0 + (anim.cx1 - anim.cx0) * e;
      v.cy = anim.cy0 + (anim.cy1 - anim.cy0) * e;
    }
    const done = t >= 1;
    if (done) anim = null;
    render(done);
    if (!done) raf = requestAnimationFrame(step);
  }

  function zoomStep(px, py, dir) {
    if (!measure()) return;
    const base = anim ? anim.z1 : v.z;
    const z1 = clamp(dir > 0 ? Math.floor(base + 1e-6) + 1 : Math.ceil(base - 1e-6) - 1, MIN_ZOOM, MAX_ZOOM);
    if (Math.abs(z1 - base) < 1e-6) return;
    const S = TILE * 2 ** v.z;
    start({ z0: v.z, z1, dur: ZOOM_MS, anchor: { fx: v.cx + (px - size.w / 2) / S, fy: v.cy + (py - size.h / 2) / S, px, py } });
  }

  function go(to, animate) {
    if (!measure()) return;
    const t = limit(to.z, to.cx, to.cy);
    if (animate) {
      start({ z0: v.z, cx0: v.cx, cy0: v.cy, z1: t.z, cx1: t.cx, cy1: t.cy, dur: FLY_MS });
    } else {
      stop();
      Object.assign(v, t);
      render(true);
    }
  }

  // ── Input: drag to pan, wheel to zoom one level at a time ──
  let drag = null;
  let wheelAcc = 0;
  let wheelAt = 0;
  const onDown = (e) => {
    if (e.target.closest?.("button")) return;
    if (e.pointerType === "mouse" && e.button !== 0) return;
    if (anim) { stop(); render(true); }
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
    if (drag && e.pointerId === drag.id) { drag = null; box.classList.remove("moving"); render(true); }
  };
  const onWheel = (e) => {
    e.preventDefault();
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1);
    wheelAcc += dy;
    const now = performance.now();
    if (Math.abs(wheelAcc) < WHEEL_STEP || now - wheelAt < WHEEL_COOLDOWN_MS) return;
    const r = box.getBoundingClientRect();
    zoomStep(e.clientX - r.left, e.clientY - r.top, wheelAcc < 0 ? 1 : -1);
    wheelAcc = 0;
    wheelAt = now;
  };
  box.addEventListener("pointerdown", onDown);
  box.addEventListener("pointermove", onMove);
  box.addEventListener("pointerup", onUp);
  box.addEventListener("pointercancel", onUp);
  box.addEventListener("wheel", onWheel, { passive: false });
  const ro = typeof ResizeObserver === "function" ? new ResizeObserver(() => { if (relayout) relayout(); else render(true); }) : null;
  ro?.observe(box);

  return {
    view: v,
    render,
    stop,
    /** Fit a { w, s, e, n } box (degrees). Fractional zoom on purpose, so the box fits exactly. */
    fit(b, pad, maxZoom, animate = false) {
      if (!measure()) return;
      const W = Math.max(size.w - 2 * pad, 1);
      const H = Math.max(size.h - 2 * pad, 1);
      const dx = Math.max((b.e - b.w) / 360, 1e-12);
      const dy = Math.max(fy(b.s) - fy(b.n), 1e-12);
      go({ z: Math.min(Math.log2(Math.min(W / (dx * TILE), H / (dy * TILE))), maxZoom), cx: (fx(b.w) + fx(b.e)) / 2, cy: (fy(b.s) + fy(b.n)) / 2 }, animate);
    },
    setView: (lat, lng, z, animate = false) => go({ z, cx: fx(lng), cy: fy(lat) }, animate),
    zoomBy: (dz) => zoomStep(size.w / 2, size.h / 2, Math.sign(dz)),
    setTilesVisible(on) { tilesOn = on; tiles.style.display = on ? "" : "none"; render(true); },
    onResize(fn) { relayout = fn; },
    tileCount: () => [...layers.values()].reduce((a, L) => a + L.imgs.size, 0),
    destroy() {
      stop();
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
