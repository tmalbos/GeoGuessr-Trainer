// Guessing helpers. A place is a square window of half_m metres around (meta.lat, meta.lon).
// Everything is in local metres (+x east, +y north, origin at the window centre).

const M_PER_DEG = 111320;

export const toLatLng = (meta, x, y) => [meta.lat + y / M_PER_DEG, meta.lon + x / (M_PER_DEG * Math.cos((meta.lat * Math.PI) / 180))];
export const coordsText = (meta, x, y) => toLatLng(meta, x, y).map((v) => v.toFixed(5)).join(", ");

export const CORRECT_FRAC = 0.03; // "found it" = within 3% of the window width (never less than 25 m)

/** GeoGuessr's formula, with the map size = the diagonal of the window: 5000 * exp(-10 * d / diagonal). */
export function judge(meta, truth, guess) {
  const dist = Math.hypot(guess.x - truth.x, guess.y - truth.y);
  const diag = meta.half_m * 2 * Math.SQRT2;
  return {
    dist,
    score: Math.round(5000 * Math.exp((-10 * dist) / diag)),
    correct: dist <= Math.max(25, CORRECT_FRAC * meta.half_m * 2),
  };
}

export const fmtDist = (m) => (m < 1000 ? `${Math.round(m)} m` : `${(m / 1000).toFixed(1)} km`);
