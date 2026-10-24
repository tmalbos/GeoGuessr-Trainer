import { useEffect, useState } from "react";
import { streetViewAvailable } from "../../lib/mapProviders.js";

// Debug mode: open the app at  #/history?debug  to get editable heading / pitch / fov under each photo.
// Read at render time (not at load), so typing it into an already-open page works.
const isDebug = () => /[?&]debug\b/.test(window.location.hash);
const FOV_AT_ZOOM_0 = 90; // keep equal to FOV_AT_ZOOM_0 in src/analysis/replay_look.py (a VERTICAL fov)
const ASPECT = 16 / 9; // keep equal to PHOTO_ASPECT in src/geo/map_providers.py
const num = (v) => (v === "" || v == null || Number.isNaN(Number(v)) ? null : Number(v));
const hfov = (vfov) => (2 * Math.atan(Math.tan((vfov * Math.PI) / 360) * ASPECT) * 180) / Math.PI;

/** Street View photo of a look event's view. Rendered by the backend (/api/streetview/photo).
 *  Shows nothing when the selected map source has no photos (OpenStreetMap, or Google without a key). */
export default function StreetPhoto({ view }) {
  const DEBUG = isDebug();
  const [ov, setOv] = useState({}); // debug overrides (strings)
  useEffect(() => { setOv({}); }, [view]);

  const v = { ...view };
  for (const k of ["heading", "pitch", "fov"]) if (num(ov[k]) != null) v[k] = num(ov[k]);

  const src = `/api/streetview/photo?${new URLSearchParams({
    lat: v.lat, lng: v.lng, heading: v.heading, pitch: v.pitch, fov: v.fov,
  })}`;
  const [state, setState] = useState("loading"); // loading | ok | error
  useEffect(() => { setState("loading"); }, [src]);

  if (!streetViewAvailable()) return null;

  return <figure className={`rr-photo ${state}`}>
    {state === "error"
      ? <p className="muted">No image available.</p>
      : <img src={src} alt="Street View of what you were looking at" onLoad={() => setState("ok")} onError={() => setState("error")} />}
    {DEBUG && <div className="rr-dbg muted">
      <div>pano {view.lat}, {view.lng} · GeoGuessr zoom ≈ {Math.log2(FOV_AT_ZOOM_0 / view.fov).toFixed(2)} · horizontal fov ≈ {hfov(v.fov).toFixed(0)}°</div>
      <div>{[["heading", "heading"], ["pitch", "pitch"], ["fov", "fov (vertical)"]].map(([k, l]) => <label key={k}>{l}
        <input type="number" step="any" value={ov[k] ?? view[k]} onChange={(e) => setOv((o) => ({ ...o, [k]: e.target.value }))} /></label>)}
      </div>
      <a href={`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${v.lat},${v.lng}&heading=${v.heading}&pitch=${v.pitch}&fov=${Math.round(Math.min(100, Math.max(10, hfov(v.fov))))}`}
        target="_blank" rel="noopener noreferrer">Same view in Google Maps ↗</a>
    </div>}
  </figure>;
}
