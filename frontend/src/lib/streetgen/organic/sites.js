// organic/sites.js: block centres for the organic style. Dart throwing with a spacing that grows toward the
// rim, then a few Lloyd steps (move each point to the middle of its cell) to even things out.
// Idea from watabou's TownGeneratorOS; code is our own.

import { Delaunay } from "d3-delaunay";

export function scatterSites(terrain, rng, block) {
  const r = rng.fork("sites");
  const LIM = 1.3;                             // sites go past the rim so the cells at the rim are closed
  const xs = terrain.polygon.map((p) => p[0]), ys = terrain.polygon.map((p) => p[1]);
  const pad = 0.4 * terrain.R0;
  const [x0, x1, y0, y1] = [Math.min(...xs) - pad, Math.max(...xs) + pad, Math.min(...ys) - pad, Math.max(...ys) + pad];
  const cell = block * 1.2, grid = new Map(), pts = [];
  const key = (cx, cy) => cx * 100003 + cy;
  const add = (x, y) => {
    pts.push(x, y);
    const k = key(Math.floor(x / cell), Math.floor(y / cell)), l = grid.get(k);
    if (l) l.push(pts.length / 2 - 1); else grid.set(k, [pts.length / 2 - 1]);
  };
  const tooClose = (x, y, sp) => {
    const rr = Math.ceil(sp / cell), cx = Math.floor(x / cell), cy = Math.floor(y / cell), sp2 = sp * sp;
    for (let i = cx - rr; i <= cx + rr; i++) for (let j = cy - rr; j <= cy + rr; j++) {
      const l = grid.get(key(i, j));
      if (l) for (const id of l) if ((pts[2 * id] - x) ** 2 + (pts[2 * id + 1] - y) ** 2 < sp2) return true;
    }
    return false;
  };
  add(terrain.center[0], terrain.center[1]);   // site 0 is downtown: it becomes the main square
  let miss = 0;
  while (miss < 500 && pts.length < 120000) {
    const x = r.range(x0, x1), y = r.range(y0, y1), rho = terrain.rho(x, y);
    if (rho > LIM) { miss++; continue; }
    if (tooClose(x, y, 0.85 * block * (1 + 1.5 * Math.min(rho, 1.3) ** 2))) miss++;
    else { add(x, y); miss = 0; }
  }
  return { pts: Float64Array.from(pts), box: [x0, y0, x1, y1] };
}

/** Lloyd relaxation. Point 0 (downtown) stays where it is. */
export function relax(pts, box, iterations) {
  let p = pts;
  for (let it = 0; it < iterations; it++) {
    const vor = new Delaunay(p).voronoi(box);
    const q = Float64Array.from(p);
    for (let i = 1; i < p.length / 2; i++) {
      const poly = vor.cellPolygon(i);
      if (!poly || poly.length < 4) continue;
      let a = 0, cx = 0, cy = 0;
      for (let k = 0; k < poly.length - 1; k++) {
        const [ax, ay] = poly[k], [bx, by] = poly[k + 1], f = ax * by - bx * ay;
        a += f; cx += (ax + bx) * f; cy += (ay + by) * f;
      }
      if (Math.abs(a) < 1e-9) continue;
      q[2 * i] = cx / (3 * a); q[2 * i + 1] = cy / (3 * a);
    }
    p = q;
  }
  return p;
}
