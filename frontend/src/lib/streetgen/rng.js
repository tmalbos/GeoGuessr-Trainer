// rng.js: seeded random numbers. Same seed, same city.

function mulberry32(a) {
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const hashStr = (s) => {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
};

/** Seed for retry number n of a city, so a failed attempt never repeats itself. */
export const deriveSeed = (seed, n) => (Math.imul(seed | 0, 2654435761) ^ Math.imul(n + 1, 0x9e3779b9)) | 0;

export function createRng(seed) {
  const next = mulberry32(Math.imul(seed | 0, 2654435761) ^ 0x9e3779b9);
  const rng = {
    next,
    range: (lo, hi) => lo + next() * (hi - lo),
    int: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)),
    pick: (arr) => arr[Math.floor(next() * arr.length)],
    chance: (p) => next() < p,
    /** An independent stream for one stage, so changing one stage never reshuffles the others. */
    fork: (label) => createRng((seed | 0) ^ hashStr(label)),
  };
  return rng;
}
