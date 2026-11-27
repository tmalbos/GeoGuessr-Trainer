// Map projections of the regional clue maps. The editors and the card draw with the SAME one (a clue stores its choice),
// and the server's pre-drawn overlay repeats these formulas in src/explore/country_clues/map_projection.py: keep them in step.
//   flat     plain map, longitudes squeezed by the cosine of the middle latitude (the default)
//   mercator like web maps (stretches towards the poles); the only one the real map can be shown behind
//   conic    Albers equal-area cone fitted to the country's latitudes: best for wide countries far from the equator
export const PROJECTIONS = [
  ["flat", "Standard"],
  ["mercator", "Mercator (like web maps)"],
  ["conic", "Conic (wide northern countries)"],
];

const W = 1000;
const PAD = 6;
const RAD = Math.PI / 180;
const MAX_LAT = 85.0511;
const EDGE_STEPS = 32;
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));

// Each builder gives { fwd([lon, lat]) -> [u, v], inv([u, v]) -> [lon, lat] } with v pointing north.
function standard(bb) {
  const k = Math.cos((((bb[1] + bb[3]) / 2) * Math.PI) / 180) || 1;
  return { fwd: ([lo, la]) => [lo * k, la], inv: ([u, v]) => [u / k, v] };
}

function mercator() {
  return {
    fwd: ([lo, la]) => [lo, Math.log(Math.tan(Math.PI / 4 + (clamp(la, -MAX_LAT, MAX_LAT) * RAD) / 2)) / RAD],
    inv: ([u, v]) => [u, (2 * Math.atan(Math.exp(v * RAD)) - Math.PI / 2) / RAD],
  };
}

function conic(bb) {
  const y0 = clamp(bb[1], -89, 89);
  const y1 = clamp(bb[3], -89, 89);
  const phi1 = (y0 + (y1 - y0) / 6) * RAD;
  const phi2 = (y1 - (y1 - y0) / 6) * RAD;
  const phi0 = ((y0 + y1) / 2) * RAD;
  const lam0 = (bb[0] + bb[2]) / 2;
  const n = (Math.sin(phi1) + Math.sin(phi2)) / 2;
  if (Math.abs(n) < 0.01) return standard(bb); // around the equator a cone is just a cylinder
  const C = Math.cos(phi1) ** 2 + 2 * n * Math.sin(phi1);
  const rho = (phi) => Math.sqrt(Math.max(C - 2 * n * Math.sin(phi), 0)) / n;
  const rho0 = rho(phi0);
  const sg = Math.sign(n);
  return {
    fwd: ([lo, la]) => {
      const r = rho(la * RAD);
      const t = n * (lo - lam0) * RAD;
      return [r * Math.sin(t), rho0 - r * Math.cos(t)];
    },
    inv: ([u, v]) => {
      const dy = rho0 - v;
      const t = Math.atan2(sg * u, sg * dy);
      const r = Math.hypot(u, dy);
      return [lam0 + t / n / RAD, Math.asin(clamp((C - (r * n) ** 2) / (2 * n), -1, 1)) / RAD];
    },
  };
}

// Extent of the lon/lat box once projected (its edges are sampled: they bend in a conic).
function extentOf(raw, bb) {
  const [x0, y0, x1, y1] = bb;
  let umin = Infinity, vmin = Infinity, umax = -Infinity, vmax = -Infinity;
  for (let i = 0; i <= EDGE_STEPS; i++) {
    const t = i / EDGE_STEPS;
    const lon = x0 + (x1 - x0) * t;
    const lat = y0 + (y1 - y0) * t;
    for (const p of [[lon, y0], [lon, y1], [x0, lat], [x1, lat]]) {
      const [u, v] = raw.fwd(p);
      umin = Math.min(umin, u); umax = Math.max(umax, u);
      vmin = Math.min(vmin, v); vmax = Math.max(vmax, v);
    }
  }
  return [umin, vmin, umax, vmax];
}

/** Fits the lon/lat box `bb` ([w, s, e, n]) into a 1000-wide map. -> { H, s, fwd, inv, perLat, toWorld? }
 *  fwd([lon, lat]) -> [x, y] in map units; inv([x, y]) -> [lon, lat]; perLat([lon, lat]) = map units per degree of latitude there.
 *  `right` pushes the drawing against the right edge instead of centring it (overlay on a clue photo).
 *  toWorld(x, y) (Mercator only) = position in the 0..1 web-map world, which the map tiles need. */
export function makeProjection(kind, bb, right = false) {
  const raw = (kind === "mercator" ? mercator : kind === "conic" ? conic : standard)(bb);
  const [umin, vmin, umax, vmax] = extentOf(raw, bb);
  const w = Math.max(umax - umin, 1e-6);
  const h = Math.max(vmax - vmin, 1e-6);
  const H = Math.round(Math.min(1400, Math.max(200, (W * h) / w)));
  const s = Math.min((W - 2 * PAD) / w, (H - 2 * PAD) / h);
  const ox = right ? W - PAD - w * s : (W - w * s) / 2;
  const oy = (H - h * s) / 2;
  const fwd = (ll) => {
    const [u, v] = raw.fwd(ll);
    return [ox + (u - umin) * s, oy + (vmax - v) * s];
  };
  const inv = ([x, y]) => raw.inv([umin + (x - ox) / s, vmax - (y - oy) / s]);
  const perLat = ([lo, la]) => {
    const a = fwd([lo, la]);
    const b = fwd([lo, la + 0.01]);
    return Math.hypot(b[0] - a[0], b[1] - a[1]) / 0.01;
  };
  const toWorld = kind === "mercator"
    ? (x, y) => [(180 + umin + (x - ox) / s) / 360, 0.5 - (vmax - (y - oy) / s) / 360]
    : undefined;
  return { kind, H, s, fwd, inv, perLat, toWorld };
}
