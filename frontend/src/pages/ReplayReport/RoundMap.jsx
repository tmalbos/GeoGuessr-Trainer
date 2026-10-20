import { useEffect, useRef, useState } from "react";
import { TILE, createMapEngine, fx, fy } from "../../lib/googleMap.js";

// The replay report map. Tiles, panning, zooming and animation live in lib/googleMap.js (shared with the
// polygonizer map). This file only draws what goes on top (path, pins, searched area, highlight).
// The overlay is drawn at some zoom and then scaled/moved with the SAME view state the tiles use, on the same frame,
// and it is redrawn exactly once the view settles, so it can never drift from the tiles.

let seq = 0;

const MAX_ZOOM = 21;
const SINGLE_POINT_ZOOM = 15;
const PATH = "#f2c14e";
const HILITE = "#3fb97a"; // selection green (same as --ok)

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

/** The report map engine: the shared engine + the overlay. `host` = { box, tiles, overlay } elements. */
export function createEngine({ box, tiles, overlay }) {
  const ov = { z: null, ox: 0, oy: 0, dirty: true };
  let data = null;
  let focus = null;

  // Runs on every frame of the shared engine, right after the tiles moved.
  const layer = (v, size, settled) => {
    if (!data) return;
    if (ov.dirty || ov.z === null || settled) { // redraw at the exact zoom
      ov.z = v.z;
      ov.ox = v.cx * TILE * 2 ** v.z;
      ov.oy = v.cy * TILE * 2 ** v.z;
      overlay.innerHTML = overlayHtml(data, focus, ov.z, ov.ox, ov.oy);
      ov.dirty = false;
    }
    const k = 2 ** (v.z - ov.z); // mid-zoom: scale what is drawn
    const S = TILE * 2 ** ov.z;
    overlay.style.setProperty("--ik", String(1 / k));
    overlay.style.transform = `translate(${size.w / 2 - (v.cx * S - ov.ox) * k}px,${size.h / 2 - (v.cy * S - ov.oy) * k}px) scale(${k})`;
  };

  const eng = createMapEngine({ box, tiles, layer, tileClass: "rr-tile", layerClass: "rr-tl" });
  return {
    ...eng,
    setData(d) { data = d; ov.dirty = true; },
    setFocus(f) { focus = f; ov.dirty = true; },
  };
}

/** `focus` (from the clicked event) is one of:
 *    { bbox: [west, south, east, north] }  exactly that area (what the player's minimap showed)
 *    { points: [[lat, lng], ...], path? }  highlight those points. A path (the steps of a run) is drawn as an
 *                                          arrow from where it started to where it ended, and the view fits the
 *                                          whole path walked in the round so the arrow is seen in context.
 *  null = the default view (real spot + guess). The map has the same aspect ratio as the minimap
 *  window used for the "seen" calculations (MAP_PX in replay_behavior.py), so a bbox fits it exactly.
 *  Changing only the focus flies the view there (animated); a new round jumps. */
export default function RoundMap({ real, guess, path, searched, focus }) {
  const box = useRef(null);
  const tiles = useRef(null);
  const overlay = useRef(null);
  const prev = useRef(null);
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
    const same = !!prev.current && prev.current.real === real && prev.current.guess === guess && prev.current.path === path && prev.current.searched === searched;
    prev.current = { real, guess, path, searched };
    const apply = (animate) => {
      try {
        if (!box.current.clientWidth) return;
        const pts = focus?.points ?? [];
        eng.setFocus(focus);
        if (!focus || (!focus.bbox && !pts.length)) {
          const all = [[real.lat, real.lng]];
          if (guess) all.push([guess.lat, guess.lng]);
          if (all.length === 1) eng.setView(real.lat, real.lng, 18, animate); else eng.fit(boundsOf(all), 56, 18, animate);
        } else if (focus.bbox) {
          const [w, s, e, n] = focus.bbox;
          eng.fit({ w, s, e, n }, 0, MAX_ZOOM, animate);
        } else if (focus.path) {
          eng.fit(boundsOf([...path.segments.flat(), ...path.jumps.flat(), ...pts]), 40, 19, animate); // everything walked in the round
        } else if (pts.length === 1) {
          eng.setView(pts[0][0], pts[0][1], SINGLE_POINT_ZOOM, animate);
        } else {
          eng.fit(boundsOf(pts), 48, 19, animate);
        }
      } catch (e) { console.error("RoundMap:", e); setErr(`Map error: ${e?.message ?? e}`); }
    };
    eng.onResize(() => apply(false)); // the map is sized by CSS (fixed ratio): refit whenever its size changes
    apply(same);
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
