import { useRef, useState } from "react";
import RegionPreview from "./RegionPreview.jsx";

const MAX_K = 16;
const STEP = 1.6;

/** RegionPreview with + / − buttons and drag-to-pan (once zoomed in). Same props as RegionPreview.
 *  The zoom is a window of 1/k of the map centred on (fx, fy), both as fractions of the map. */
export default function ZoomableRegion(props) {
  const [zoom, setZoom] = useState({ k: 1, fx: 0.5, fy: 0.5 });
  const pan = useRef({ drag: null, moved: false });
  const by = (f) => setZoom((z) => {
    const k = Math.min(MAX_K, Math.max(1, z.k * f));
    return k === 1 ? { k, fx: 0.5, fy: 0.5 } : { ...z, k };
  });
  return <div className="cl-zoomwrap">
    <RegionPreview {...props} zoom={zoom} onZoom={setZoom} pan={pan} />
    <div className="cl-zoom">
      <button type="button" aria-label="Zoom in" title="Zoom in" disabled={zoom.k >= MAX_K} onClick={() => by(STEP)}>+</button>
      <button type="button" aria-label="Zoom out" title="Zoom out" disabled={zoom.k <= 1} onClick={() => by(1 / STEP)}>−</button>
    </div>
  </div>;
}
