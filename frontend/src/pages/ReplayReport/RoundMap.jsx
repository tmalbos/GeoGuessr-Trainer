import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";

const pin = (color) => L.divIcon({
  className: "rr-pin",
  html: `<svg viewBox="0 0 24 32" width="28" height="38" aria-hidden="true"><path d="M12 31C12 31 2 19.5 2 11.5a10 10 0 0 1 20 0C22 19.5 12 31 12 31Z" fill="${color}" stroke="#fff" stroke-width="2"/><circle cx="12" cy="11.5" r="3.6" fill="#fff"/></svg>`,
  iconSize: [28, 38], iconAnchor: [14, 38], tooltipAnchor: [0, -34],
});

const PATH = "#f2c14e";

// "Result" frames your guess against the actual spot. "Path" frames where you walked in street view.
export default function RoundMap({ real, guess, path, searched }) {
  const el = useRef(null);
  const fitRef = useRef(null);
  const [mode, setMode] = useState("result");
  const modeRef = useRef(mode);
  modeRef.current = mode;
  const hasPath = path.segments.length > 0;

  useEffect(() => {
    const map = L.map(el.current, { worldCopyJump: true });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19, attribution: "© OpenStreetMap contributors",
    }).addTo(map);

    if (searched.length) {
      L.polygon(searched, { color: "#6cc0e5", weight: 1, fillColor: "#2d7ea3", fillOpacity: 0.2 })
        .bindTooltip("Area you searched on the map", { sticky: true }).addTo(map);
    }
    for (const seg of path.segments) {
      L.polyline(seg, { color: PATH, weight: 3, opacity: 0.9 }).bindTooltip("Your path", { sticky: true }).addTo(map);
    }
    for (const j of path.jumps) {
      L.polyline(j, { color: PATH, weight: 2, opacity: 0.6, dashArray: "4 6" })
        .bindTooltip("You jumped back to an earlier spot", { sticky: true }).addTo(map);
    }
    if (guess) {
      L.polyline([[real.lat, real.lng], [guess.lat, guess.lng]], { color: "#8b98a8", weight: 2, dashArray: "6 6" }).addTo(map);
      L.marker([guess.lat, guess.lng], { icon: pin("#ff7357") })
        .bindTooltip("Your guess", { permanent: true, direction: "top" }).addTo(map);
    }
    L.marker([real.lat, real.lng], { icon: pin("#3fb97a"), zIndexOffset: 500 })
      .bindTooltip("Actual", { permanent: true, direction: "top" }).addTo(map);

    const result = L.latLngBounds([[real.lat, real.lng]]);
    if (guess) result.extend([guess.lat, guess.lng]);
    const walked = L.latLngBounds([[real.lat, real.lng]]);
    for (const seg of path.segments) walked.extend(seg);

    fitRef.current = (m) => map.fitBounds(m === "path" ? walked : result, {
      padding: [56, 56], maxZoom: m === "path" ? 19 : 18, animate: false,
    });
    fitRef.current(modeRef.current);
    return () => map.remove();
  }, [real, guess, path, searched]);

  useEffect(() => { fitRef.current?.(mode); }, [mode]);

  return <div className="rr-map-wrap">
    <div ref={el} className="rr-map" role="img" aria-label="Map with the actual location, your guess and your path" />
    {hasPath && <div className="rr-toggle" role="group" aria-label="Map view">
      {[["result", "Result"], ["path", "Path"]].map(([k, l]) =>
        <button key={k} type="button" className={mode === k ? "on" : ""} aria-pressed={mode === k} onClick={() => setMode(k)}>{l}</button>)}
    </div>}
  </div>;
}
