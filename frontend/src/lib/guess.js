// Guessing helpers. A place is a square window of half_m metres around (meta.lat, meta.lon).
// Local coordinates (metres, +x east, +y north) map to lat/lon with a flat approximation around the window centre,
// which is accurate enough for areas of a few tens of km and is exactly invertible.

const M_PER_DEG = 111320;
const COS = (meta) => Math.cos((meta.lat * Math.PI) / 180);

export const toLatLng = (meta, x, y) => [meta.lat + y / M_PER_DEG, meta.lon + x / (M_PER_DEG * COS(meta))];
export const toLocal = (meta, lat, lon) => ({ x: (lon - meta.lon) * M_PER_DEG * COS(meta), y: (lat - meta.lat) * M_PER_DEG });
export const areaBounds = (meta) => [toLatLng(meta, -meta.half_m, -meta.half_m), toLatLng(meta, meta.half_m, meta.half_m)];
export const coordsText = (meta, x, y) => toLatLng(meta, x, y).map((v) => v.toFixed(5)).join(", ");

export const CORRECT_FRAC = 0.03; // "found it" = within 3% of the window width (never less than 25 m)

/** GeoGuessr's formula, with the map size = the diagonal of the window: 5000 * exp(-10 * d / diagonal). */
export function judge(meta, truth, lat, lon) {
  const g = toLocal(meta, lat, lon);
  const dist = Math.hypot(g.x - truth.x, g.y - truth.y);
  const diag = meta.half_m * 2 * Math.SQRT2;
  return {
    dist,
    score: Math.round(5000 * Math.exp((-10 * dist) / diag)),
    correct: dist <= Math.max(25, CORRECT_FRAC * meta.half_m * 2),
  };
}

export const fmtDist = (m) => (m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`);
