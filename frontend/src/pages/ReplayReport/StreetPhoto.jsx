import { useEffect, useState } from "react";

/** Street View photo of a look event's view. Rendered by the backend (/api/streetview/photo). */
export default function StreetPhoto({ view }) {
  const src = `/api/streetview/photo?${new URLSearchParams({
    lat: view.lat, lng: view.lng, heading: view.heading, pitch: view.pitch, fov: view.fov,
  })}`;
  const [state, setState] = useState("loading"); // loading | ok | error
  useEffect(() => { setState("loading"); }, [src]);
  return <figure className={`rr-photo ${state}`}>
    {state === "error"
      ? <p className="muted">No image available.</p>
      : <img src={src} alt="Street View of what you were looking at" onLoad={() => setState("ok")} onError={() => setState("error")} />}
  </figure>;
}
