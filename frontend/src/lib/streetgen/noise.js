// noise.js: smooth value noise (reviewed and kept from the old generator). Output in [-1, 1].

const s01 = (t) => { t = t < 0 ? 0 : t > 1 ? 1 : t; return t * t * (3 - 2 * t); };

function hash2(x, y, s) {
  let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ Math.imul(s, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** Smooth 2D noise; `wavelength` is in metres. */
export function noise2(x, y, seed, wavelength) {
  x /= wavelength; y /= wavelength;
  const ix = Math.floor(x), iy = Math.floor(y), fx = s01(x - ix), fy = s01(y - iy);
  const a = hash2(ix, iy, seed), b = hash2(ix + 1, iy, seed), c = hash2(ix, iy + 1, seed), d = hash2(ix + 1, iy + 1, seed);
  const top = a + (b - a) * fx, bot = c + (d - c) * fx;
  return (top + (bot - top) * fy) * 2 - 1;
}
