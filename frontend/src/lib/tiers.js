// The tier ladder comes from the backend (`tiers` in /analysis/filters): [{ label, max }], lowest first.
// Segments are equal width so the top tiers (which are tiny score ranges) stay visible.

// Mirrors dist_to_score in src/analysis/scoring.py.
export const kmToScore = (km) => Math.round(5000 * Math.exp(-0.000673 * km));

// 0 = red end of the ladder, 1 = top.
export const tierHue = (i, n) => Math.round((i / Math.max(1, n - 1)) * 170);
export const tierIndex = (tiers, label) => tiers.findIndex((t) => t.label === label);

/** Position (0..1) of a score along the ladder. */
export function scorePos(tiers, s) {
  const n = tiers.length;
  let lo = 0;
  for (let i = 0; i < n; i++) {
    const hi = tiers[i].max;
    if (s <= hi || i === n - 1) return Math.min(1, (i + Math.max(0, (s - lo) / (hi - lo))) / n);
    lo = hi;
  }
  return 1;
}
