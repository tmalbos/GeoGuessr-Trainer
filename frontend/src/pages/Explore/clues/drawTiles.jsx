import { useEffect, useRef, useState } from "react";
import { attribution, maxTileZoom, tileUrl } from "../../../lib/mapProviders.js";

// Shared by DrawMap and ShadeDraw. The draw maps use a Web Mercator projection (the same one the map tiles use),
// so the real map can be switched on behind the drawing and both stay aligned at any zoom.
const W = 1000; // keep equal to W in DrawMap.jsx / ShadeDraw.jsx
const PAD = 6;
const RAD = Math.PI / 180;
const MAX_LAT = 85.0511;
const MAX_TILES = 80;

// Mercator y of a latitude, in "degrees" (so x and y share one scale).
const merc = (lat) => Math.log(Math.tan(Math.PI / 4 + (Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * RAD) / 2)) / RAD;

export function makeProjection(bb) {
  const [x0, y0, x1, y1] = bb;
  const top = merc(y1);
  const w = Math.max(x1 - x0, 1e-6);
  const h = Math.max(top - merc(y0), 1e-6);
  const H = Math.round(Math.min(1400, Math.max(200, (W * h) / w)));
  const s = Math.min((W - 2 * PAD) / w, (H - 2 * PAD) / h);
  const ox = (W - w * s) / 2;
  const oy = (H - h * s) / 2;
  return {
    H, s,
    fwd: ([lon, lat]) => [ox + (lon - x0) * s, oy + (top - merc(lat)) * s],
    inv: ([x, y]) => [x0 + (x - ox) / s, (2 * Math.atan(Math.exp((top - (y - oy) / s) * RAD)) - Math.PI / 2) / RAD],
    // map units <-> position in the Web Mercator world (0..1 on both axes), which is what tiles are laid out in
    toWorld: (x, y) => [(180 + x0 + (x - ox) / s) / 360, 0.5 - (top - (y - oy) / s) / 360],
  };
}

export const TilesIcon = () => <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6l6-2 6 2 6-2v14l-6 2-6-2-6 2z" /><path d="M9 4v14M15 6v14" /></svg>;

/** The map chosen in Settings, drawn behind the (transparent) drawing SVG. Must be the first child of the `.dm` box:
 *  it fills it, and the box has the same shape as the SVG's view, so tiles are placed in % of the box. */
export function TileBackdrop({ proj, view }) {
  const box = useRef(null);
  const [px, setPx] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver !== "function") return undefined;
    const read = () => setPx(el.clientWidth);
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const [fx0, fy0] = proj.toWorld(view.x, view.y);
  const [fx1, fy1] = proj.toWorld(view.x + view.w, view.y + (view.w * proj.H) / W);
  const spanX = fx1 - fx0;
  const spanY = fy1 - fy0;
  const tiles = [];
  if (px > 0 && spanX > 0 && spanY > 0) {
    const z = Math.max(0, Math.min(maxTileZoom(), Math.round(Math.log2(px / (256 * spanX)))));
    const n = 2 ** z;
    const ya = Math.max(0, Math.floor(fy0 * n));
    const yb = Math.min(n - 1, Math.floor(fy1 * n));
    for (let x = Math.floor(fx0 * n); x <= Math.floor(fx1 * n); x++) {
      for (let y = ya; y <= yb; y++) {
        const url = tileUrl(x, y, z);
        if (!url || tiles.length >= MAX_TILES) continue;
        tiles.push(<img key={`${z}/${x}/${y}`} src={url} alt="" draggable={false} style={{
          left: `${((x / n - fx0) / spanX) * 100}%`, top: `${((y / n - fy0) / spanY) * 100}%`,
          width: `calc(${(1 / n / spanX) * 100}% + 1px)`, height: `calc(${(1 / n / spanY) * 100}% + 1px)`,
        }} />);
      }
    }
  }
  return <div className="dm-tiles" ref={box} aria-hidden="true">
    {tiles}
    <span className="dm-attr">{attribution()}</span>
  </div>;
}

// ── The real map behind the drawing, in a projection that is not Mercator ──
// Map tiles are Web Mercator pictures. In any other projection the visible part is cut into GRID x GRID cells and every
// cell is drawn as two triangles, each one stretching its piece of the tiles with a single straight (affine) transform.
// This only changes how the editor's backdrop looks: the drawing, what is saved and the card never see it.
const GRID = 16;
const TILE_PX = 256;
const MAX_WARP_TILES = 150;
const tileImgs = new Map(); // tile url -> Image, kept between redraws

const mercWorld = (lon, lat) => {
  const s = Math.sin(Math.max(-MAX_LAT, Math.min(MAX_LAT, lat)) * RAD);
  return [(lon + 180) / 360, 0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)];
};

// The transform [a, b, c, d, e, f] (canvas setTransform order) that takes the 3 points `from` onto the 3 points `to`.
function affineOf([p0, p1, p2], [q0, q1, q2]) {
  const dx1 = p1[0] - p0[0], dy1 = p1[1] - p0[1], dx2 = p2[0] - p0[0], dy2 = p2[1] - p0[1];
  const D = dx1 * dy2 - dx2 * dy1;
  if (Math.abs(D) < 1e-9) return null;
  const du1 = q1[0] - q0[0], dv1 = q1[1] - q0[1], du2 = q2[0] - q0[0], dv2 = q2[1] - q0[1];
  const a = (du1 * dy2 - du2 * dy1) / D, c = (du2 * dx1 - du1 * dx2) / D;
  const b = (dv1 * dy2 - dv2 * dy1) / D, d = (dv2 * dx1 - dv1 * dx2) / D;
  return [a, b, c, d, q0[0] - a * p0[0] - c * p0[1], q0[1] - b * p0[0] - d * p0[1]];
}

function tileImage(url, onLoad) {
  let img = tileImgs.get(url);
  if (!img) {
    if (tileImgs.size > 400) tileImgs.clear();
    img = new Image();
    img.src = url;
    tileImgs.set(url, img);
  }
  if (!img.complete) img.addEventListener("load", onLoad, { once: true });
  return img.complete && img.naturalWidth > 0 ? img : null;
}

/** Same job as TileBackdrop (same place in the `.dm` box), for a projection without `toWorld`: `proj.inv` says which
 *  longitude / latitude every point of the view is, and the tiles are stretched onto that. */
export function WarpedBackdrop({ proj, view }) {
  const box = useRef(null);
  const cv = useRef(null);
  const [px, setPx] = useState(0);
  const [tick, setTick] = useState(0); // a tile finished loading: draw again
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver !== "function") return undefined;
    const read = () => setPx(el.clientWidth);
    read();
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const c = cv.current;
    if (!c || px <= 0) return;
    const dpr = window.devicePixelRatio || 1;
    const w = Math.round(px * dpr);
    const h = Math.max(1, Math.round((px * proj.H * dpr) / W));
    c.width = w; // also clears it
    c.height = h;
    const g = c.getContext("2d");
    const vh = (view.w * proj.H) / W;
    // where a canvas point is on the web-map world (0..1 on both axes)
    const world = (cx, cy) => {
      const [lo, la] = proj.inv([view.x + (cx / w) * view.w, view.y + (cy / h) * vh]);
      return mercWorld(lo, la);
    };
    const a = world(0, 0), b = world(w, h), c2 = world(w, 0), d = world(0, h);
    const span = Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]), Math.hypot(c2[0] - d[0], c2[1] - d[1]), 1e-9);
    const z = Math.max(0, Math.min(maxTileZoom(), Math.round(Math.log2(Math.hypot(w, h) / dpr / (TILE_PX * span)))));
    const n = 2 ** z;
    const S = TILE_PX * n;
    const src = []; // the same grid of points on the tile world (in pixels at zoom z) ...
    const dst = []; // ... and on the canvas
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let j = 0; j <= GRID; j++) {
      for (let i = 0; i <= GRID; i++) {
        const cx = (i * w) / GRID;
        const cy = (j * h) / GRID;
        const [wx, wy] = world(cx, cy);
        src.push([wx * S, wy * S]);
        dst.push([cx, cy]);
        x0 = Math.min(x0, wx * S); x1 = Math.max(x1, wx * S);
        y0 = Math.min(y0, wy * S); y1 = Math.max(y1, wy * S);
      }
    }
    const tx0 = Math.floor(x0 / TILE_PX), tx1 = Math.floor(x1 / TILE_PX);
    const ty0 = Math.max(0, Math.floor(y0 / TILE_PX)), ty1 = Math.min(n - 1, Math.floor(y1 / TILE_PX));
    if ((tx1 - tx0 + 1) * (ty1 - ty0 + 1) > MAX_WARP_TILES) return;
    const onLoad = () => setTick((t) => t + 1);
    const tiles = new Map();
    for (let ty = ty0; ty <= ty1; ty++) {
      for (let tx = tx0; tx <= tx1; tx++) {
        const url = tileUrl(tx, ty, z);
        const img = url ? tileImage(url, onLoad) : null;
        if (img) tiles.set(`${tx},${ty}`, img);
      }
    }
    const at = (i, j) => j * (GRID + 1) + i;
    const triangle = (A, B, C) => {
      const m = affineOf([src[A], src[B], src[C]], [dst[A], dst[B], dst[C]]);
      if (!m) return;
      const sx = [src[A][0], src[B][0], src[C][0]];
      const sy = [src[A][1], src[B][1], src[C][1]];
      const gx = (dst[A][0] + dst[B][0] + dst[C][0]) / 3;
      const gy = (dst[A][1] + dst[B][1] + dst[C][1]) / 3;
      g.save();
      g.setTransform(1, 0, 0, 1, 0, 0);
      g.beginPath(); // the triangle, grown a little so neighbouring triangles leave no seam
      [A, B, C].forEach((k, idx) => {
        const dx = dst[k][0] - gx, dy = dst[k][1] - gy, len = Math.hypot(dx, dy) || 1;
        const qx = dst[k][0] + (dx / len) * 0.75, qy = dst[k][1] + (dy / len) * 0.75;
        if (idx) g.lineTo(qx, qy); else g.moveTo(qx, qy);
      });
      g.closePath();
      g.clip();
      g.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
      const ax1 = Math.floor(Math.max(...sx) / TILE_PX), ay1 = Math.floor(Math.max(...sy) / TILE_PX);
      for (let ty = Math.floor(Math.min(...sy) / TILE_PX); ty <= ay1; ty++) {
        for (let tx = Math.floor(Math.min(...sx) / TILE_PX); tx <= ax1; tx++) {
          const img = tiles.get(`${tx},${ty}`);
          if (img) g.drawImage(img, tx * TILE_PX, ty * TILE_PX, TILE_PX + 1, TILE_PX + 1);
        }
      }
      g.restore();
    };
    for (let j = 0; j < GRID; j++) {
      for (let i = 0; i < GRID; i++) {
        triangle(at(i, j), at(i + 1, j), at(i, j + 1));
        triangle(at(i + 1, j), at(i + 1, j + 1), at(i, j + 1));
      }
    }
  }, [px, proj, view, tick]);

  return <div className="dm-tiles" ref={box} aria-hidden="true">
    <canvas ref={cv} style={{ display: "block", width: "100%", height: "100%" }} />
    <span className="dm-attr">{attribution()}</span>
  </div>;
}
