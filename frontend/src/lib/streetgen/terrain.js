// terrain.js: where the city is. An irregular blob (a stretched, rotated ellipse with a wobbly rim) around a
// downtown point, with room around it. Streets get sparser towards the rim (bigger blocks).
// Water will carve this footprint in phase 4.

import { boundsFor } from "./params.js";

export function createTerrain(P, rng) {
  const r = rng.fork("terrain");
  const H = P.sizeKm * 500;
  const R0 = 0.52 * H;                       // base radius: the city fills about half the map width
  const stretch = r.range(1, 1.18), rot = r.range(0, Math.PI);
  const sx = Math.sqrt(stretch), sy = 1 / sx;
  const cx = r.range(-0.05, 0.05) * H, cy = r.range(-0.05, 0.05) * H;
  const amp = 0.6 + 0.8 * P.irregularity;
  const harm = [[1, 0.1], [2, 0.08], [3, 0.06], [5, 0.035]].map(([k, a]) => ({ k, a: a * amp, p: r.range(0, 2 * Math.PI) }));
  const radial = (th) => 1 + harm.reduce((s, h) => s + h.a * Math.sin(h.k * th + h.p), 0);
  const c = Math.cos(rot), s = Math.sin(rot);
  const toFrame = (x, y) => { const dx = x - cx, dy = y - cy; return [(dx * c + dy * s) / sx, (-dx * s + dy * c) / sy]; };
  /** 0 at downtown, 1 on the rim of the footprint. */
  const rho = (x, y) => { const [u, v] = toFrame(x, y); return Math.hypot(u, v) / (R0 * radial(Math.atan2(v, u))); };

  const polygon = [];
  for (let i = 0; i < 72; i++) {
    const th = (i / 72) * 2 * Math.PI, rr = R0 * radial(th);
    const u = rr * Math.cos(th) * sx, v = rr * Math.sin(th) * sy;
    polygon.push([cx + u * c - v * s, cy + u * s + v * c]);
  }
  return {
    H, bounds: boundsFor(P.sizeKm), center: [cx, cy], R0, polygon, rho,
    inside: (x, y) => rho(x, y) < 1,
    /** How much bigger than the base block a block is allowed to be here. */
    spacingFactor: (x, y) => 1 + 1.5 * Math.min(rho(x, y), 1.3) ** 2,
  };
}
