import { useEffect, useState } from "react";

// Debug mode: open the app at  #/history?debug  to get editable heading / pitch / fov under each photo.
// Read at render time (not at load), so typing it into an already-open page works.
const isDebug = () => /[?&]debug\b/.test(window.location.hash);
const FOV_AT_ZOOM_0 = 90; // keep equal to FOV_AT_ZOOM_0 in src/analysis/replay_look.py
const num = (v) => (v === "" || v == null || Number.isNaN(Number(v)) ? null : Number(v));

/** Street View photo of a look event's view. Rendered by the backend (/api/streetview/photo). */
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

  return <figure className={`rr-photo ${state}`}>
    {state === "error"
      ? <p className="muted">No image available.</p>
      : <img src={src} alt="Street View of what you were looking at" onLoad={() => setState("ok")} onError={() => setState("error")} />}
    {DEBUG && <div className="rr-dbg muted">
      <div>pano {view.lat}, {view.lng} · GeoGuessr zoom ≈ {Math.log2(FOV_AT_ZOOM_0 / view.fov).toFixed(2)}</div>
      <div>{["heading", "pitch", "fov"].map((k) => <label key={k}>{k}
        <input type="number" step="any" value={ov[k] ?? view[k]} onChange={(e) => setOv((o) => ({ ...o, [k]: e.target.value }))} /></label>)}
      </div>
      <a href={`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${v.lat},${v.lng}&heading=${v.heading}&pitch=${v.pitch}&fov=${Math.min(100, Math.max(10, v.fov))}`}
        target="_blank" rel="noopener noreferrer">Same view in Google Maps ↗</a>
    </div>}
  </figure>;
}
