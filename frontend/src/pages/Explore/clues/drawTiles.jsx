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
