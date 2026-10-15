import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef, useState } from "react";
import { num } from "../../lib/format.js";
import { areaBounds, fmtDist, judge, toLatLng } from "../../lib/guess.js";

// GeoGuessr-style minimap: small in the corner, grows on hover, click to drop a pin, then Guess.
// The map can never show anything outside the generated area. Tiles have no labels so names don't give the place away.

const TILES = "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png";
const GUESS = "#ff7357", TRUTH = "#3fb97a";

const pinIcon = (color) => L.divIcon({
  className: "mm-pin", iconSize: [28, 38], iconAnchor: [14, 36],
  html: `<svg viewBox="0 0 28 38" width="28" height="38"><path d="M14 1C7 1 1.5 6.5 1.5 13.3 1.5 22.5 14 36 14 36S26.5 22.5 26.5 13.3C26.5 6.5 21 1 14 1z" fill="${color}" stroke="#0d1117" stroke-width="2"/><circle cx="14" cy="13.5" r="4.5" fill="#fff"/></svg>`,
});

export default function Minimap({ meta, truth, result, onGuess, onNext, busy }) {
  const el = useRef(null);
  const st = useRef({ map: null, marker: null });
  const doneRef = useRef(false);
  const [pin, setPin] = useState(null);
  const [open, setOpen] = useState(false);
  const done = !!result;
  doneRef.current = done;

  // Create the map once per round (the parent remounts this component with a new key).
  useEffect(() => {
    const bounds = L.latLngBounds(areaBounds(meta));
    const map = L.map(el.current, { zoomControl: false, keyboard: false, maxBounds: bounds, maxBoundsViscosity: 1, zoomSnap: 0.25 });
    L.tileLayer(TILES, { subdomains: "abcd", maxZoom: 20, attribution: "© OpenStreetMap © CARTO" }).addTo(map);
    st.current.map = map;

    // Smallest zoom at which the area still fills the view: you can't zoom out past the generated area.
    const relimit = () => {
      map.invalidateSize({ animate: false });
      map.setMinZoom(0);
      const z = map.getBoundsZoom(bounds, true);
      map.setMinZoom(z);
      if (map.getZoom() < z) map.setZoom(z, { animate: false });
      map.panInsideBounds(bounds, { animate: false });
    };
    map.setView(bounds.getCenter(), 0, { animate: false });
    relimit();

    map.on("click", (e) => {
      if (doneRef.current) return;
      if (st.current.marker) st.current.marker.setLatLng(e.latlng);
      else st.current.marker = L.marker(e.latlng, { icon: pinIcon(GUESS), interactive: false }).addTo(map);
      setPin([e.latlng.lat, e.latlng.lng]);
    });

    const ro = new ResizeObserver(relimit);
    ro.observe(el.current);
    return () => { ro.disconnect(); map.remove(); st.current = { map: null, marker: null }; };
  }, [meta]);

  // After the guess: show the real spot, a line between the two, and frame both.
  useEffect(() => {
    const map = st.current.map;
    if (!map || !result) return undefined;
    const t = toLatLng(meta, truth.x, truth.y);
    L.marker(t, { icon: pinIcon(TRUTH), interactive: false }).addTo(map);
    L.polyline([result.guess, t], { color: "#e6edf3", weight: 2, dashArray: "6 6" }).addTo(map);
    const id = setTimeout(() => { // wait for the panel to finish growing
      map.invalidateSize({ animate: false });
      map.fitBounds(L.latLngBounds([result.guess, t]).pad(0.5), { maxZoom: 17, animate: false });
    }, 280);
    return () => clearTimeout(id);
  }, [result, meta, truth]);

  const guess = () => onGuess({ guess: pin, ...judge(meta, truth, pin[0], pin[1]) });

  return <div className={`mm ${done ? "result" : ""} ${open ? "open" : ""}`}>
    <div className="mm-map" ref={el} />
    {!done && <button type="button" className="mm-toggle" aria-label={open ? "Shrink the map" : "Enlarge the map"} onClick={() => setOpen((o) => !o)}>{open ? "↘" : "↖"}</button>}
    <div className="mm-foot">
      {done ? <>
        <div className="mm-res">
          <b className={result.correct ? "ok" : ""}>{result.correct ? "Found it!" : `${fmtDist(result.dist)} away`}</b>
          <span>{result.correct ? `${fmtDist(result.dist)} off · ` : ""}{num(result.score)} points</span>
        </div>
        <button type="button" className="btn" onClick={onNext} disabled={busy}>{busy ? "Finding a place…" : "Next place"}</button>
      </> : <button type="button" className="btn mm-guess" disabled={!pin} onClick={guess}>{pin ? "Guess" : "Place your pin on the map"}</button>}
    </div>
  </div>;
}
