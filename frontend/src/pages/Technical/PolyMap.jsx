import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { useEffect, useRef } from "react";

/** features: GeoJSON FeatureCollection | null. pts: [{ lat, lng, color }]. fitKey: refit the view when it changes. */
export default function PolyMap({ features, pts, basemap, fitKey }) {
  const el = useRef(null);
  const ref = useRef({});

  useEffect(() => {
    const map = L.map(el.current, { worldCopyJump: true, preferCanvas: true, zoomSnap: 0.25 }).setView([20, 10], 2);
    map.createPane("poly").style.zIndex = 410;
    map.createPane("dots").style.zIndex = 420;
    ref.current = {
      map,
      tiles: L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19, attribution: "© OpenStreetMap contributors" }),
      poly: L.layerGroup().addTo(map),
      dots: L.layerGroup().addTo(map),
    };
    const ro = new ResizeObserver(() => map.invalidateSize({ animate: false }));
    ro.observe(el.current);
    return () => { ro.disconnect(); map.remove(); ref.current = {}; };
  }, []);

  useEffect(() => {
    const { map, tiles } = ref.current;
    if (basemap) tiles.addTo(map); else tiles.remove();
  }, [basemap]);

  useEffect(() => {
    const { poly } = ref.current;
    poly.clearLayers();
    if (!features?.features.length) return;
    L.geoJSON(features, {
      pane: "poly",
      style: (f) => ({ pane: "poly", color: "#1a1a1a", weight: 1.2, opacity: 0.7, fillColor: f.properties.color, fillOpacity: 0.6 }),
      onEachFeature: (f, l) => l.bindTooltip(f.properties.tag, { sticky: true }),
    }).addTo(poly);
  }, [features]);

  useEffect(() => {
    const { dots } = ref.current;
    dots.clearLayers();
    for (const p of pts) {
      L.circleMarker([p.lat, p.lng], { pane: "dots", radius: 2.5, weight: 0.8, color: "#000", opacity: 0.7, fillColor: p.color, fillOpacity: 1 }).addTo(dots);
    }
  }, [pts]);

  // Fit to the places as soon as the selection changes (they are known instantly; polygons follow).
  const ptsRef = useRef(pts);
  ptsRef.current = pts;
  useEffect(() => {
    const p = ptsRef.current;
    if (!p.length) return;
    ref.current.map.fitBounds(L.latLngBounds(p.map((x) => [x.lat, x.lng])), { padding: [30, 30], maxZoom: 10, animate: false });
  }, [fitKey]);

  return <div ref={el} className="tp-map" role="img" aria-label="Map with the selected places and their polygons" />;
}
