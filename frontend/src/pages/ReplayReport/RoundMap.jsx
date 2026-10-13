import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";

let seq = 0;

// The player's pin: a round avatar with a white ring and a small tail (tip = the guessed spot).
const guessIcon = () => {
  const id = `rr-av-${++seq}`;
  return L.divIcon({
    className: "rr-pin",
    html: `<svg viewBox="0 0 40 50" width="36" height="45" aria-hidden="true">
      <defs><clipPath id="${id}"><circle cx="20" cy="20" r="16"/></clipPath></defs>
      <path d="M20 49L12 35H28Z" fill="#fff"/>
      <circle cx="20" cy="20" r="19" fill="#fff"/>
      <circle cx="20" cy="20" r="16" fill="#f2b880"/>
      <g clip-path="url(#${id})"><circle cx="20" cy="16" r="6.5" fill="#6f4a2f"/><ellipse cx="20" cy="36" rx="12" ry="9.5" fill="#6f4a2f"/></g>
    </svg>`,
    iconSize: [36, 45], iconAnchor: [18, 44],
  });
};

// The real spot: GeoGuessr's own flag pin (frontend/public/pins/correct-location.webp), ringed in white by CSS.
const realIcon = L.divIcon({
  className: "rr-pin rr-real",
  html: '<img src="/pins/correct-location.webp" alt="" width="32" height="32" />',
  iconSize: [36, 36], iconAnchor: [18, 18],
});

const PATH = "#f2c14e";
const HILITE = "#ff7357";
const SINGLE_POINT_ZOOM = 15;

/** `focus` (from the clicked event) is one of:
 *    { bbox: [west, south, east, north] }  exactly that area (what the player's minimap showed)
 *    { points: [[lat, lng], ...], path? }  fit those points and highlight them (path = the steps of a run)
 *  null = the default view (real spot + guess). The map has the same aspect ratio as the minimap
 *  window used for the "seen" calculations (MAP_PX in replay_behavior.py), so a bbox fits it exactly. */
export default function RoundMap({ real, guess, path, searched, focus }) {
  const el = useRef(null);
  const mapRef = useRef(null);
  const hlRef = useRef(null);
  const defaultRef = useRef(() => {});
  const applyRef = useRef(() => {});

  useEffect(() => {
    // zoomSnap 0: fractional zoom, so a bbox fills the map exactly instead of snapping to a whole zoom.
    const map = L.map(el.current, { worldCopyJump: true, zoomSnap: 0 });
    mapRef.current = map;
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
      // Dotted gray line between the pins, like GeoGuessr's result map.
      L.polyline([[real.lat, real.lng], [guess.lat, guess.lng]], {
        color: "#5f6368", weight: 3, opacity: 0.95, dashArray: "1 8", lineCap: "round",
      }).addTo(map);
      L.marker([guess.lat, guess.lng], { icon: guessIcon(), zIndexOffset: 400 }).addTo(map);
    }
    L.marker([real.lat, real.lng], { icon: realIcon, zIndexOffset: 500 }).addTo(map);
    hlRef.current = L.layerGroup().addTo(map); // highlight of the selected event

    const bounds = L.latLngBounds([[real.lat, real.lng]]);
    if (guess) bounds.extend([guess.lat, guess.lng]);
    defaultRef.current = () => map.fitBounds(bounds, { padding: [56, 56], maxZoom: 18, animate: false });

    // The map is sized by CSS (fixed ratio), so refit whenever its pixel size changes.
    const ro = new ResizeObserver(() => { map.invalidateSize({ animate: false }); applyRef.current(); });
    ro.observe(el.current);
    return () => { ro.disconnect(); map.remove(); mapRef.current = null; };
  }, [real, guess, path, searched]);

  useEffect(() => {
    const map = mapRef.current;
    const hl = hlRef.current;
    const apply = () => {
      hl.clearLayers();
      if (map.getSize().x === 0) return;
      if (!focus) { defaultRef.current(); return; }
      if (focus.bbox) {
        const [w, s, e, n] = focus.bbox;
        map.fitBounds([[s, w], [n, e]], { animate: false });
        return;
      }
      const pts = focus.points ?? [];
      if (!pts.length) { defaultRef.current(); return; }
      if (focus.path) {
        L.polyline(pts, { color: HILITE, weight: 5, opacity: 0.95 }).addTo(hl);
        pts.forEach((p, i) => L.circleMarker(p, {
          radius: i === 0 ? 7 : 4, color: "#fff", weight: 2, fillColor: i === 0 ? "#fff" : HILITE, fillOpacity: 1,
        }).addTo(hl));
      } else {
        pts.forEach((p) => L.circleMarker(p, { radius: 9, color: "#fff", weight: 3, fillColor: HILITE, fillOpacity: 0.9 }).addTo(hl));
      }
      if (pts.length === 1) map.setView(pts[0], SINGLE_POINT_ZOOM, { animate: false });
      else map.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 19, animate: false });
    };
    applyRef.current = apply;
    apply();
  }, [focus, real, guess, path, searched]);

  return <div ref={el} className="rr-map" role="img" aria-label="Map with the actual location, your guess and your path" />;
}
