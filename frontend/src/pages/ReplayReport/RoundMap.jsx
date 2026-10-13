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
const HILITE = "#3fb97a"; // selection green (same as --ok)
const SINGLE_POINT_ZOOM = 15;

const arrowIcon = (angle) => L.divIcon({
  className: "rr-arrow",
  html: `<svg viewBox="0 0 24 24" width="28" height="28" style="transform:rotate(${angle}deg)" aria-hidden="true">
    <path d="M3 4L22 12L3 20L8 12Z" fill="${HILITE}" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>`,
  iconSize: [28, 28], iconAnchor: [14, 14],
});

/** `focus` (from the clicked event) is one of:
 *    { bbox: [west, south, east, north] }  exactly that area (what the player's minimap showed)
 *    { points: [[lat, lng], ...], path? }  highlight those points. A path (the steps of a run) is drawn as an
 *                                          arrow from where it started to where it ended, and the view fits the
 *                                          whole path walked in the round so the arrow is seen in context.
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
        .addTo(map);
    }
    for (const seg of path.segments) {
      L.polyline(seg, { color: PATH, weight: 3, opacity: 0.9 }).addTo(map);
    }
    for (const j of path.jumps) {
      L.polyline(j, { color: PATH, weight: 2, opacity: 0.6, dashArray: "4 6" })
        .addTo(map);
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
    const walked = [...path.segments.flat(), ...path.jumps.flat()]; // everything walked in the round

    // The highlight. Redrawn after every zoom so the arrowhead keeps pointing the right way on screen.
    const draw = () => {
      hl.clearLayers();
      const pts = focus?.points;
      if (!pts?.length) return;
      if (!focus.path) {
        pts.forEach((p) => L.circleMarker(p, { radius: 9, color: "#fff", weight: 3, fillColor: HILITE, fillOpacity: 0.9 }).addTo(hl));
        return;
      }
      L.polyline(pts, { color: HILITE, weight: 5, opacity: 0.95 }).addTo(hl);
      L.circleMarker(pts[0], { radius: 6, color: HILITE, weight: 3, fillColor: "#fff", fillOpacity: 1 }).addTo(hl);
      const to = map.latLngToContainerPoint(pts[pts.length - 1]);
      const from = pts.slice(0, -1).reverse().map((p) => map.latLngToContainerPoint(p))
        .find((p) => Math.hypot(to.x - p.x, to.y - p.y) >= 4);
      if (from) {
        const angle = (Math.atan2(to.y - from.y, to.x - from.x) * 180) / Math.PI;
        L.marker(pts[pts.length - 1], { icon: arrowIcon(angle), interactive: false, keyboard: false }).addTo(hl);
      }
    };

    const apply = () => {
      hl.clearLayers();
      if (map.getSize().x === 0) return;
      const pts = focus?.points ?? [];
      if (!focus || (!focus.bbox && !pts.length)) { defaultRef.current(); return; }
      if (focus.bbox) {
        const [w, s, e, n] = focus.bbox;
        map.fitBounds([[s, w], [n, e]], { animate: false });
        return;
      }
      if (focus.path) {
        map.fitBounds(L.latLngBounds([...walked, ...pts]), { padding: [40, 40], maxZoom: 19, animate: false });
      } else if (pts.length === 1) {
        map.setView(pts[0], SINGLE_POINT_ZOOM, { animate: false });
      } else {
        map.fitBounds(L.latLngBounds(pts), { padding: [48, 48], maxZoom: 19, animate: false });
      }
      draw();
    };
    applyRef.current = apply;
    map.on("zoomend", draw);
    apply();
    return () => { map.off("zoomend", draw); };
  }, [focus, real, guess, path, searched]);

  return <div ref={el} className="rr-map" role="img" aria-label="Map with the actual location, your guess and your path" />;
}
