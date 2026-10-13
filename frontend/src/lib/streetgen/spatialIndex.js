// spatialIndex.js: uniform grids for "what is near here?" questions.

const key = (cx, cy) => (cx + 524288) * 1048576 + (cy + 524288);
const PAD = 0.01;

/** Grid of segments. Items are any number/string; one item may own many segments. */
export function createGrid(cell = 100) {
  const cells = new Map();   // key -> Set(item)
  const owned = new Map();   // item -> Set(key)

  function boxCells(minx, miny, maxx, maxy, fn) {
    const x0 = Math.floor(minx / cell), x1 = Math.floor(maxx / cell);
    const y0 = Math.floor(miny / cell), y1 = Math.floor(maxy / cell);
    for (let cx = x0; cx <= x1; cx++) for (let cy = y0; cy <= y1; cy++) fn(key(cx, cy));
  }
  // Long segments are cut into pieces no longer than a cell, so a piece's box touches at most 4 cells.
  function pieces(ax, ay, bx, by, fn) {
    const n = Math.max(1, Math.ceil(Math.hypot(bx - ax, by - ay) / cell));
    for (let k = 0; k < n; k++) {
      const x0 = ax + ((bx - ax) * k) / n, y0 = ay + ((by - ay) * k) / n;
      const x1 = ax + ((bx - ax) * (k + 1)) / n, y1 = ay + ((by - ay) * (k + 1)) / n;
      fn(Math.min(x0, x1), Math.min(y0, y1), Math.max(x0, x1), Math.max(y0, y1));
    }
  }
  return {
    insertSegment(item, ax, ay, bx, by) {
      let mine = owned.get(item);
      if (!mine) owned.set(item, (mine = new Set()));
      pieces(ax, ay, bx, by, (a, b, c, d) => boxCells(a - PAD, b - PAD, c + PAD, d + PAD, (k) => {
        let s = cells.get(k);
        if (!s) cells.set(k, (s = new Set()));
        s.add(item);
        mine.add(k);
      }));
    },
    remove(item) {
      const mine = owned.get(item);
      if (!mine) return;
      for (const k of mine) { const s = cells.get(k); if (s) { s.delete(item); if (!s.size) cells.delete(k); } }
      owned.delete(item);
    },
    queryBox(minx, miny, maxx, maxy) {
      const out = new Set();
      boxCells(minx - PAD, miny - PAD, maxx + PAD, maxy + PAD, (k) => { const s = cells.get(k); if (s) for (const i of s) out.add(i); });
      return out;
    },
    querySegment(ax, ay, bx, by, pad = 0) {
      const out = new Set();
      pieces(ax, ay, bx, by, (a, b, c, d) => boxCells(a - pad - PAD, b - pad - PAD, c + pad + PAD, d + pad + PAD, (k) => {
        const s = cells.get(k);
        if (s) for (const i of s) out.add(i);
      }));
      return out;
    },
  };
}

/** Grid of points with ids. */
export function createPointGrid(cell = 50) {
  const cells = new Map();
  const pos = new Map();
  return {
    insert(id, x, y) {
      pos.set(id, [x, y]);
      const k = key(Math.floor(x / cell), Math.floor(y / cell));
      const l = cells.get(k);
      if (l) l.push(id); else cells.set(k, [id]);
    },
    near(x, y, r) {
      const out = [], r2 = r * r;
      for (let cx = Math.floor((x - r) / cell); cx <= Math.floor((x + r) / cell); cx++) {
        for (let cy = Math.floor((y - r) / cell); cy <= Math.floor((y + r) / cell); cy++) {
          const l = cells.get(key(cx, cy));
          if (!l) continue;
          for (const id of l) { const p = pos.get(id); if ((p[0] - x) ** 2 + (p[1] - y) ** 2 <= r2) out.push(id); }
        }
      }
      return out;
    },
  };
}
