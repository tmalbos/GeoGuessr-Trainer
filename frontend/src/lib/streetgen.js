// Random street network generator (pure JS, no React). Everything is in metres.
//
// Pipeline (ELI5):
//  1. NEIGHBOURHOODS: scatter "districts", each with its own style (grid / organic / suburb / radial),
//     orientation and block size. Together they define a smooth direction field: "which way do streets run here?"
//  2. ARTERIALS: a skeleton of main roads (links between sub-centres, exits to the map edge, radial spokes, a ring).
//  3. COLLECTORS then LOCAL STREETS: traced as evenly spaced streamlines of the direction field
//     (two perpendicular families = the two directions of a block). Wide spacing -> collectors, tight -> locals.
//  4. CLEAN-UP: line ends snap to nearby roads, crossings become intersections, tiny stubs are removed,
//     and a main road may never dead-end (it joins another main road, reaches the map edge, or is demoted).

export const STYLES = ["grid", "organic", "suburb", "radial"];

export const DEFAULTS = {
  seed: 1, sizeKm: 6, block: 100,
  districts: 14, centreStyle: "grid", centreSize: 0.3,
  wGrid: 0.5, wOrganic: 0.3, wSuburb: 0.1, wRadial: 0.1, alignment: 0.5,
  regularity: 0.6, curviness: 0.4, aspect: 1, connectivity: 1,
  falloff: 0.5, patchiness: 0.2, stretch: 1, stretchAngle: 0, squareness: 0, gridAngle: 0,
  collectorEvery: 6, hubs: 6, exits: 4, spokes: 0, spokeAngle: 45, ring: 0,
};

export const PRESETS = [
  { key: "laplata", label: "La Plata", params: {
    seed: 1, sizeKm: 7, block: 115, districts: 10, centreStyle: "grid", centreSize: 0.78,
    wGrid: 1, wOrganic: 0.1, wSuburb: 0.15, wRadial: 0, alignment: 0.25,
    regularity: 1, curviness: 0, aspect: 1, connectivity: 1,
    falloff: 0.15, patchiness: 0.1, stretch: 1, stretchAngle: 35, squareness: 1, gridAngle: 35,
    collectorEvery: 6, hubs: 0, exits: 0, spokes: 4, spokeAngle: 45, ring: 0.78 } },
  { key: "canberra", label: "Canberra", params: {
    seed: 4, sizeKm: 11, block: 100, districts: 24, centreStyle: "grid", centreSize: 0.1,
    wGrid: 0.05, wOrganic: 0.1, wSuburb: 0.85, wRadial: 0, alignment: 0.3,
    regularity: 0.3, curviness: 0.9, aspect: 1, connectivity: 0.85,
    falloff: 0.5, patchiness: 0.75, stretch: 1.5, stretchAngle: -20, squareness: 0, gridAngle: 0,
    collectorEvery: 7, hubs: 9, exits: 5, spokes: 0, spokeAngle: 0, ring: 0 } },
  { key: "wellington", label: "Wellington", params: {
    seed: 2, sizeKm: 7, block: 85, districts: 16, centreStyle: "grid", centreSize: 0.18,
    wGrid: 0.25, wOrganic: 0.6, wSuburb: 0.15, wRadial: 0, alignment: 0.4,
    regularity: 0.4, curviness: 0.8, aspect: 1.3, connectivity: 0.75,
    falloff: 0.55, patchiness: 0.55, stretch: 2.2, stretchAngle: -30, squareness: 0, gridAngle: 20,
    collectorEvery: 6, hubs: 5, exits: 3, spokes: 0, spokeAngle: 0, ring: 0 } },
  { key: "nagpur", label: "Nagpur", params: {
    seed: 9, sizeKm: 6, block: 70, districts: 30, centreStyle: "radial", centreSize: 0.12,
    wGrid: 0.45, wOrganic: 0.3, wSuburb: 0.05, wRadial: 0.2, alignment: 0.15,
    regularity: 0.45, curviness: 0.5, aspect: 1.2, connectivity: 0.9,
    falloff: 0.25, patchiness: 0.1, stretch: 1.1, stretchAngle: 0, squareness: 0, gridAngle: 10,
    collectorEvery: 5, hubs: 7, exits: 4, spokes: 6, spokeAngle: 10, ring: 0.55 } },
  { key: "nairobi", label: "Nairobi", params: {
    seed: 11, sizeKm: 14, block: 150, districts: 26, centreStyle: "grid", centreSize: 0.08,
    wGrid: 0.25, wOrganic: 0.4, wSuburb: 0.3, wRadial: 0.05, alignment: 0.2,
    regularity: 0.4, curviness: 0.6, aspect: 1.2, connectivity: 0.55,
    falloff: 0.6, patchiness: 0.65, stretch: 1.2, stretchAngle: 30, squareness: 0, gridAngle: 15,
    collectorEvery: 5, hubs: 10, exits: 6, spokes: 0, spokeAngle: 0, ring: 0.5 } },
  { key: "amsterdam", label: "Amsterdam", params: {
    seed: 6, sizeKm: 9, block: 80, districts: 18, centreStyle: "radial", centreSize: 0.3,
    wGrid: 0.6, wOrganic: 0.15, wSuburb: 0.1, wRadial: 0.15, alignment: 0.3,
    regularity: 0.75, curviness: 0.3, aspect: 1.5, connectivity: 0.95,
    falloff: 0.35, patchiness: 0.25, stretch: 1, stretchAngle: 0, squareness: 0.2, gridAngle: 20,
    collectorEvery: 6, hubs: 6, exits: 3, spokes: 5, spokeAngle: 0, ring: 0.62 } },
];

export function randomParams() {
  const r = (a, b) => a + Math.random() * (b - a);
  const q = (v, st) => Math.round(v / st) * st;
  const pick = (a) => a[Math.floor(Math.random() * a.length)];
  const planned = Math.random() < 0.35;
  return {
    seed: Math.floor(Math.random() * 99999), sizeKm: q(r(4, 12), 0.5), block: q(r(70, 150), 5),
    districts: Math.round(r(8, 28)), centreStyle: pick(planned ? ["grid"] : STYLES), centreSize: q(r(0.08, planned ? 0.7 : 0.35), 0.05),
    wGrid: planned ? q(r(0.6, 1), 0.05) : q(r(0, 0.7), 0.05), wOrganic: q(r(0, 0.6), 0.05),
    wSuburb: q(r(0, 0.6), 0.05), wRadial: q(r(0, 0.3), 0.05), alignment: q(r(0, 0.8), 0.05),
    regularity: planned ? q(r(0.8, 1), 0.05) : q(r(0.2, 0.8), 0.05), curviness: planned ? q(r(0, 0.3), 0.05) : q(r(0.3, 1), 0.05),
    aspect: q(r(1, 1.8), 0.1), connectivity: q(r(0.5, 1), 0.05),
    falloff: q(r(0.1, 0.7), 0.05), patchiness: q(r(0, 0.8), 0.05), stretch: q(r(1, 2), 0.1), stretchAngle: Math.round(r(-90, 90)),
    squareness: planned ? q(r(0.5, 1), 0.05) : q(r(0, 0.3), 0.05), gridAngle: Math.round(r(0, 90)),
    collectorEvery: Math.round(r(4, 8)), hubs: Math.round(r(0, 10)), exits: Math.round(r(2, 6)),
    spokes: Math.random() < 0.4 ? Math.round(r(3, 8)) : 0, spokeAngle: Math.round(r(0, 90)), ring: Math.random() < 0.5 ? q(r(0.4, 0.8), 0.05) : 0,
  };
}

// ── helpers ───────────────────────────────────────────────────────────────────
const TAU = Math.PI * 2;
const clamp = (v, a, b) => (v < a ? a : v > b ? b : v);
const s01 = (t) => { t = clamp(t, 0, 1); return t * t * (3 - 2 * t); };

function mulberry32(a) {
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function hash2(x, y, s) {
  let h = Math.imul(x, 374761393) ^ Math.imul(y, 668265263) ^ Math.imul(s, 1442695041);
  h = Math.imul(h ^ (h >>> 13), 1274126177);
  h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}
/** Smooth value noise in [-1, 1]; `l` = wavelength in metres. */
function noise(x, y, seed, l) {
  x /= l; y /= l;
  const ix = Math.floor(x), iy = Math.floor(y), fx = s01(x - ix), fy = s01(y - iy);
  const a = hash2(ix, iy, seed), b = hash2(ix + 1, iy, seed), c = hash2(ix, iy + 1, seed), d = hash2(ix + 1, iy + 1, seed);
  const top = a + (b - a) * fx, bot = c + (d - c) * fx;
  return (top + (bot - top) * fy) * 2 - 1;
}

/** Spatial hash of vertex ids (coordinates live in the shared vx/vy arrays). */
class Hash {
  constructor(cell, vx, vy, vcls) { this.c = cell; this.m = new Map(); this.vx = vx; this.vy = vy; this.vcls = vcls; }
  key(cx, cy) { return (cx + 16384) * 32768 + (cy + 16384); }
  add(id) {
    const k = this.key(Math.floor(this.vx[id] / this.c), Math.floor(this.vy[id] / this.c));
    const l = this.m.get(k);
    if (l) l.push(id); else this.m.set(k, [id]);
  }
  /** True if a vertex blocks (x,y): same-class vertices within r, other classes within rOther (default r). */
  any(x, y, r, cls, rOther) {
    const c = this.c, r2 = r * r;
    const ro2 = rOther === undefined ? r2 : rOther * rOther;
    const rmax = Math.max(r, rOther === undefined ? r : rOther);
    if (rmax > r) return this.anyWide(x, y, rmax, r2, ro2, cls);
    for (let cx = Math.floor((x - r) / c), x1 = Math.floor((x + r) / c); cx <= x1; cx++) {
      for (let cy = Math.floor((y - r) / c), y1 = Math.floor((y + r) / c); cy <= y1; cy++) {
        const l = this.m.get(this.key(cx, cy));
        if (!l) continue;
        for (let i = 0; i < l.length; i++) {
          const dx = this.vx[l[i]] - x, dy = this.vy[l[i]] - y;
          if (dx * dx + dy * dy < r2) return true;
        }
      }
    }
    return false;
  }
  anyWide(x, y, rmax, r2, ro2, cls) {
    const c = this.c;
    for (let cx = Math.floor((x - rmax) / c), x1 = Math.floor((x + rmax) / c); cx <= x1; cx++) {
      for (let cy = Math.floor((y - rmax) / c), y1 = Math.floor((y + rmax) / c); cy <= y1; cy++) {
        const l = this.m.get(this.key(cx, cy));
        if (!l) continue;
        for (let i = 0; i < l.length; i++) {
          const dx = this.vx[l[i]] - x, dy = this.vy[l[i]] - y, d = dx * dx + dy * dy;
          if (d < (this.vcls[l[i]] === cls ? r2 : ro2)) return true;
        }
      }
    }
    return false;
  }
  nearest(x, y, r, pred) {
    const c = this.c;
    let best = -1, bd = r * r;
    for (let cx = Math.floor((x - r) / c), x1 = Math.floor((x + r) / c); cx <= x1; cx++) {
      for (let cy = Math.floor((y - r) / c), y1 = Math.floor((y + r) / c); cy <= y1; cy++) {
        const l = this.m.get(this.key(cx, cy));
        if (!l) continue;
        for (let i = 0; i < l.length; i++) {
          const id = l[i], dx = this.vx[id] - x, dy = this.vy[id] - y, d = dx * dx + dy * dy;
          if (d < bd && (!pred || pred(id))) { bd = d; best = id; }
        }
      }
    }
    return best;
  }
}

const segCross = (ax, ay, bx, by, cx, cy, dx, dy) => {
  const d1x = bx - ax, d1y = by - ay, d2x = dx - cx, d2y = dy - cy, den = d1x * d2y - d1y * d2x;
  if (Math.abs(den) < 1e-9) return false;
  const t = ((cx - ax) * d2y - (cy - ay) * d2x) / den, u = ((cx - ax) * d1y - (cy - ay) * d1x) / den;
  return t > 0.01 && t < 0.99 && u > 0.01 && u < 0.99;
};

// ── generator ─────────────────────────────────────────────────────────────────
export function generate(P) {
  const t0 = Date.now();
  const rnd = mulberry32(Math.imul(P.seed | 0, 2654435761) ^ 0x9e3779b9);
  const S = Array.from({ length: 16 }, () => (rnd() * 1e6) | 0);
  const H = P.sizeKm * 500;          // half of the map square
  const R = H / 1.12;                // city radius
  const block = P.block;
  const ds = clamp(block * 0.12, 8, 20); // streamline step
  const deg = Math.PI / 180;

  // footprint (stretched / rotated / squarish disc)
  const fa = P.stretchAngle * deg, fc = Math.cos(fa), fs = Math.sin(fa);
  const sx = Math.sqrt(P.stretch), sy = 1 / sx;
  const pwC = 2 + P.squareness * 8;
  const rhoOf = (x, y) => {
    const u = (x * fc + y * fs) / (R * sx), v = (-x * fs + y * fc) / (R * sy);
    return pwC === 2 ? Math.hypot(u, v) : (Math.abs(u) ** pwC + Math.abs(v) ** pwC) ** (1 / pwC);
  };
  const fromFoot = (u, v) => [u * fc - v * fs, u * fs + v * fc];
  const wA = R * 0.1;

  /** How built-up is it here (0 = nothing, 1 = full)? Drives where streets exist. */
  const density = (x, y) => {
    const wx = x + wA * noise(x, y, S[0], R * 0.7), wy = y + wA * noise(x, y, S[1], R * 0.7);
    const rho = rhoOf(wx, wy);
    let d = (1 - P.falloff * rho * rho) * (1 - s01((rho - 0.86) / 0.14));
    if (P.patchiness > 0 && d > 0) {
      const n = 0.5 + 0.5 * (0.7 * noise(x, y, S[2], R * 0.6) + 0.3 * noise(x, y, S[3], R * 0.25));
      d *= 1 - s01((n - (0.95 - 0.55 * P.patchiness)) / 0.14) * s01((rho - 0.1) / 0.3);
    }
    return d;
  };

  // 1. neighbourhoods ─────────────────────────────────────────────────────────
  const reg = P.regularity, cv = P.curviness, baseAngle = P.gridAngle * deg;
  const districts = [];
  const mk = (x, y, style, r, a, sq) => {
    const d = { x, y, style, r, a, ca: Math.cos(a), sa: Math.sin(a), pw: 2 + sq * 8, ex: 1, sd: (rnd() * 1e6) | 0, mult: 1, m: 0 };
    if (style === "grid") { d.A = (1 - reg) * 0.5 + cv * 0.2; d.lam = R * 0.5; d.skew = (1 - reg) * 0.25; d.join = 1; d.mult = 1; d.asp = P.aspect; }
    else if (style === "organic") { d.A = 0.35 + 0.7 * cv; d.lam = 750 - 250 * cv; d.skew = 0.2 + 0.4 * (1 - reg); d.join = 0.95; d.stop = 1 / 1800; d.mult = 0.9 + rnd() * 0.3; d.asp = 1 + (P.aspect - 1) * 0.4; }
    else if (style === "suburb") { d.A = 0.5 + cv * 0.7; d.lam = 800 - 250 * cv; d.skew = 0.3 + 0.3 * (1 - reg); d.join = 0.8; d.stop = 1 / 420; d.mult = 1.2 + rnd() * 0.3; d.asp = 1; }
    else { d.A = (1 - reg) * 0.3; d.lam = R * 0.4; d.skew = 0.1 * (1 - reg); d.join = 1; d.mult = 0.85; d.asp = districts.length === 0 ? 1.1 : 2.4; }
    d.stop = d.stop || 0;
    d.mult *= 0.9 + rnd() * 0.25;
    districts.push(d);
    return d;
  };
  const wts = [P.wGrid, P.wOrganic, P.wSuburb, P.wRadial];
  const wsum = wts.reduce((a, b) => a + b, 0) || 1;
  const pickStyle = () => { let r = rnd() * wsum; for (let i = 0; i < 4; i++) if ((r -= wts[i]) <= 0) return STYLES[i]; return "grid"; };
  const nDist = Math.max(1, Math.round(P.districts));
  mk(0, 0, P.centreStyle, Math.max(R * P.centreSize, 4 * block), baseAngle, P.squareness);
  const meanR = R / Math.sqrt(nDist);
  for (let tries = 0; districts.length < nDist && tries < nDist * 60; tries++) {
    const ang = rnd() * TAU, rr = Math.sqrt(rnd());
    const [x, y] = fromFoot(Math.cos(ang) * rr * R * sx, Math.sin(ang) * rr * R * sy);
    const style = pickStyle();
    const r = meanR * (0.7 + 0.6 * rnd()) * (style === "radial" ? 0.65 : 1) * (1 + 0.35 * rr);
    if (districts.some((d) => Math.hypot(x - d.x, y - d.y) < (r + d.r) * 0.65)) continue;
    { const c0 = districts[0], dx = x - c0.x, dy = y - c0.y, u = dx * c0.ca + dy * c0.sa, v = -dx * c0.sa + dy * c0.ca;
      const m0 = (Math.abs(u) ** c0.pw + Math.abs(v) ** c0.pw) ** (1 / c0.pw) / c0.r;
      if (m0 < 1.05) continue; }
    const a = rnd() < P.alignment ? baseAngle + (rnd() < 0.5 ? 0 : Math.PI / 2) : rnd() * Math.PI;
    const d = mk(x, y, style, r, a, style === "grid" ? rnd() * 0.6 : 0);
    d.ex = 1 + (rnd() - 0.5) * 0.6;
  }

  // direction field blended from all neighbourhoods (tensor blend, so angles mix sensibly)
  const F = { th: 0, mag: 0, skew: 0, block: 1, join: 1, aspect: 1, stop: 0 };
  let cA = 1; // curvature multiplier (collectors are smoother than local streets)
  const KW = 1 / 0.16;
  function field(x, y) {
    const wx = x + wA * noise(x, y, S[4], R * 0.45), wy = y + wA * noise(x, y, S[5], R * 0.45);
    let mmin = 1e9;
    for (const d of districts) {
      const dx = wx - d.x, dy = wy - d.y;
      const u = (dx * d.ca + dy * d.sa) / d.ex, v = (-dx * d.sa + dy * d.ca) * d.ex;
      const m = (d.pw === 2 ? Math.sqrt(u * u + v * v) : (Math.abs(u) ** d.pw + Math.abs(v) ** d.pw) ** (1 / d.pw)) / d.r;
      d.m = m;
      if (m < mmin) mmin = m;
    }
    let sw = 0, c2 = 0, s2 = 0, sk = 0, lb = 0, jn = 0, la = 0, st = 0;
    for (const d of districts) {
      const dm = d.m - mmin;
      if (dm > 0.7) continue;
      const w = Math.exp(-dm * Math.min(12.5, d.r / 110));
      let th;
      if (d.style === "radial") th = Math.atan2(wy - d.y, wx - d.x) + d.A * noise(wx, wy, d.sd, d.lam);
      else if (d.style === "grid") th = d.a + d.A * cA * noise(wx, wy, d.sd, d.lam);
      else th = d.a + d.A * cA * (noise(wx, wy, d.sd, d.lam) + 0.2 * noise(wx, wy, d.sd + 7, d.lam * 0.43));
      c2 += w * Math.cos(2 * th); s2 += w * Math.sin(2 * th); sw += w;
      sk += w * d.skew; lb += w * Math.log(d.mult); jn += w * d.join; la += w * Math.log(d.asp); st += w * d.stop;
    }
    F.th = 0.5 * Math.atan2(s2, c2);
    F.mag = Math.hypot(c2, s2) / sw;
    F.skew = sk / sw; F.join = jn / sw; F.stop = st / sw; F.aspect = Math.exp(la / sw);
    const rho = rhoOf(x, y);
    F.block = Math.exp(lb / sw) * (1 + 1.2 * P.falloff * rho * rho);
  }
  let DX = 1, DY = 0;
  function dirAt(x, y, fam, px, py) {
    field(x, y);
    const a = F.th + (fam === 1 ? Math.PI / 2 + F.skew : 0);
    let c = Math.cos(a), s = Math.sin(a);
    if (px * c + py * s < 0) { c = -c; s = -s; }
    DX = c; DY = s;
  }

  // graph storage ─────────────────────────────────────────────────────────────
  const vx = [], vy = [], vline = [], vidx = [], vcls = [], vb = [];
  let ea = [], eb = [], ec = [], rem = [];
  let lineId = 0;
  const addV = (x, y, line, idx, cls, border) => { vx.push(x); vy.push(y); vline.push(line); vidx.push(idx); vcls.push(cls); vb.push(border ? 1 : 0); return vx.length - 1; };
  const addE = (a, b, c) => { ea.push(a); eb.push(b); ec.push(c); rem.push(0); };
  const elen = (e) => Math.hypot(vx[ea[e]] - vx[eb[e]], vy[ea[e]] - vy[eb[e]]);
  const cell = Math.max(20, block * 0.6);
  const HF = [new Hash(cell, vx, vy, vcls), new Hash(cell, vx, vy, vcls)];
  const ends = [];

  // 2. arterial skeleton ──────────────────────────────────────────────────────
  const rayToBorder = (x, y, dx, dy) => {
    let t = 1e9;
    if (dx > 1e-9) t = Math.min(t, (H - x) / dx); else if (dx < -1e-9) t = Math.min(t, (-H - x) / dx);
    if (dy > 1e-9) t = Math.min(t, (H - y) / dy); else if (dy < -1e-9) t = Math.min(t, (-H - y) / dy);
    return [x + dx * t, y + dy * t];
  };
  const bendPts = (ax, ay, bx, by, bend) => {
    const len = Math.hypot(bx - ax, by - ay) || 1, n = Math.max(2, Math.ceil(len / 25));
    const c1 = rnd() * 2 - 1, c2 = rnd() * 2 - 1, nx = -(by - ay) / len, ny = (bx - ax) / len, out = [];
    for (let i = 0; i <= n; i++) {
      const t = i / n, off = len * bend * (c1 * Math.sin(Math.PI * t) + 0.5 * c2 * Math.sin(2 * Math.PI * t));
      out.push(ax + (bx - ax) * t + nx * off, ay + (by - ay) * t + ny * off);
    }
    return out;
  };
  function addPoly(pts, cls, sv, ev, bS, bE, both) {
    const id = lineId++, n = pts.length / 2;
    let prev = -1, first = -1;
    for (let i = 0; i < n; i++) {
      let v;
      if (i === 0 && sv >= 0) v = sv;
      else if (i === n - 1 && ev >= 0) v = ev;
      else { v = addV(pts[2 * i], pts[2 * i + 1], id, i, cls, (i === 0 && bS) || (i === n - 1 && bE)); if (both) { HF[0].add(v); HF[1].add(v); } }
      if (prev >= 0) addE(prev, v, cls);
      prev = v;
      if (i === 0) first = v;
    }
    return { first, last: prev };
  }
  const hubVertex = (x, y) => { const v = addV(x, y, -1, 0, 2, 0); HF[0].add(v); HF[1].add(v); return v; };

  const bend = 0.03 + 0.09 * cv;
  const hubPts = [[0, 0]];
  {
    const cands = districts.slice(1).filter((d) => density(d.x, d.y) > 0.4);
    for (let k = 0; k < Math.round(P.hubs) && cands.length; k++) {
      let bi = -1, bs = -1;
      cands.forEach((d, i) => {
        const md = Math.min(...hubPts.map((h) => Math.hypot(h[0] - d.x, h[1] - d.y)));
        if (md > bs && md > R * 0.2) { bs = md; bi = i; }
      });
      if (bi < 0) break;
      hubPts.push([cands[bi].x, cands[bi].y]);
      cands.splice(bi, 1);
    }
  }
  const nh = hubPts.length;
  const needArterials = P.hubs > 0 || P.exits > 0 || P.spokes > 0 || P.ring > 0;
  if (needArterials) {
    const hv = hubPts.map(([x, y]) => hubVertex(x, y));
    // Gabriel graph over hubs (planar, connected), then make sure every hub has two links
    const hed = [];
    for (let i = 0; i < nh; i++) for (let j = i + 1; j < nh; j++) {
      const mx = (hubPts[i][0] + hubPts[j][0]) / 2, my = (hubPts[i][1] + hubPts[j][1]) / 2;
      const r2 = ((hubPts[i][0] - hubPts[j][0]) ** 2 + (hubPts[i][1] - hubPts[j][1]) ** 2) / 4;
      let ok = true;
      for (let k = 0; k < nh && ok; k++) if (k !== i && k !== j && (hubPts[k][0] - mx) ** 2 + (hubPts[k][1] - my) ** 2 < r2) ok = false;
      if (ok) hed.push([i, j]);
    }
    const hdeg = new Array(nh).fill(0);
    hed.forEach(([i, j]) => { hdeg[i]++; hdeg[j]++; });
    for (let i = 0; i < nh; i++) {
      while (nh > 2 && hdeg[i] < 2) {
        let bj = -1, bd = Infinity;
        for (let j = 0; j < nh; j++) {
          if (j === i || hed.some(([a, b]) => (a === i && b === j) || (a === j && b === i))) continue;
          const d = Math.hypot(hubPts[i][0] - hubPts[j][0], hubPts[i][1] - hubPts[j][1]);
          if (d < bd && !hed.some(([a, b]) => segCross(hubPts[i][0], hubPts[i][1], hubPts[j][0], hubPts[j][1], hubPts[a][0], hubPts[a][1], hubPts[b][0], hubPts[b][1]))) { bd = d; bj = j; }
        }
        if (bj < 0) break;
        hed.push([i, bj]); hdeg[i]++; hdeg[bj]++;
      }
    }
    for (const [i, j] of hed) {
      addPoly(bendPts(hubPts[i][0], hubPts[i][1], hubPts[j][0], hubPts[j][1], bend), 2, hv[i], hv[j], false, false, true);
    }
    // exits from outer hubs (or from the centre when there are no hubs)
    const exitFrom = [];
    if (nh === 1) {
      const base = rnd() * TAU;
      for (let k = 0; k < Math.round(P.exits); k++) exitFrom.push([0, base + (k / Math.max(1, Math.round(P.exits))) * TAU + (rnd() - 0.5) * 0.5]);
    } else {
      const order = [...Array(nh).keys()].slice(1).sort((a, b) => (hdeg[a] - hdeg[b]) || (Math.hypot(...hubPts[b]) - Math.hypot(...hubPts[a])));
      const must = order.filter((i) => hdeg[i] < 2);
      const take = new Set(must);
      for (const i of order) if (take.size < P.exits) take.add(i);
      for (const i of take) exitFrom.push([i, Math.atan2(hubPts[i][1], hubPts[i][0]) + (rnd() - 0.5) * 0.4]);
    }
    for (const [i, ang] of exitFrom) {
      const [bx, by] = rayToBorder(hubPts[i][0], hubPts[i][1], Math.cos(ang), Math.sin(ang));
      addPoly(bendPts(hubPts[i][0], hubPts[i][1], bx, by, bend * 0.7), 2, hv[i], -1, false, true, true);
    }
    // radial spokes from the centre (e.g. La Plata's diagonals)
    const nSp = Math.round(P.spokes);
    for (let k = 0; k < nSp; k++) {
      const ang = baseAngle + P.spokeAngle * deg + (k / nSp) * TAU;
      const [bx, by] = rayToBorder(0, 0, Math.cos(ang), Math.sin(ang));
      addPoly(bendPts(0, 0, bx, by, bend * 0.5), 2, hv[0], -1, false, true, true);
    }
    // ring road
    if (P.ring > 0) {
      const ph = [rnd() * TAU, rnd() * TAU, rnd() * TAU];
      const per = TAU * R * P.ring * Math.max(sx, sy), n = Math.max(24, Math.ceil(per / 30));
      const pts = [];
      for (let i = 0; i < n; i++) {
        const t = (i / n) * TAU, c = Math.cos(t), s = Math.sin(t);
        const nrm = pwC === 2 ? 1 : (Math.abs(c) ** pwC + Math.abs(s) ** pwC) ** (1 / pwC);
        const rr = P.ring * (1 + 0.07 * (0.5 * Math.sin(2 * t + ph[0]) + 0.3 * Math.sin(3 * t + ph[1]) + 0.2 * Math.sin(5 * t + ph[2])));
        const [x, y] = fromFoot((c / nrm) * rr * R * sx, (s / nrm) * rr * R * sy);
        pts.push(x, y);
      }
      const { first, last } = addPoly(pts, 2, -1, -1, false, false, true);
      addE(last, first, 2);
    }
  }

  // 3. collectors, then local streets: evenly spaced streamlines ──────────────
  const MAXV = 450000;
  function runPass(cls) {
    const mul = cls === 1 ? Math.max(2, P.collectorEvery) : 1;
    const thr = cls === 1 ? 0.12 : 0.3;
    const dsep0 = block * mul;
    const minLen = cls === 1 ? Math.min(1.6 * dsep0, 0.5 * R) : 1.6 * block;
    const maxSteps = Math.ceil((2.4 * H) / ds);
    const stepSeed = Math.max(1, Math.round(dsep0 / ds));
    cA = cls === 1 ? 0.45 : 1;
    const q = [[], []], qh = [0, 0];
    q[0].push(0, 0); q[1].push(0, 0);
    const cand = [];
    const cs = 1.4 * dsep0;
    for (let x = -H + cs / 2; x < H; x += cs) for (let y = -H + cs / 2; y < H; y += cs) if (density(x, y) > thr) cand.push(x + (rnd() - 0.5) * cs * 0.5, y + (rnd() - 0.5) * cs * 0.5);
    const ox = [], oy = [], oa = [];

    function walk(x, y, fam, sgn, res) {
      dirAt(x, y, fam, 0, 0);
      let dxp = DX * sgn, dyp = DY * sgn;
      for (let k = 1; k <= maxSteps; k++) {
        dirAt(x, y, fam, dxp, dyp);
        const ax = DX, ay = DY;
        dirAt(x + (ax * ds) / 2, y + (ay * ds) / 2, fam, ax, ay);
        if (F.mag < 0.12) return 0;
        const bx = DX, by = DY;
        if (bx * dxp + by * dyp < 0.5) return 0;
        const nx = x + bx * ds, ny = y + by * ds;
        if (Math.abs(nx) > H || Math.abs(ny) > H) {
          let t = 1;
          if (nx > H) t = Math.min(t, (H - x) / (nx - x)); else if (nx < -H) t = Math.min(t, (-H - x) / (nx - x));
          if (ny > H) t = Math.min(t, (H - y) / (ny - y)); else if (ny < -H) t = Math.min(t, (-H - y) / (ny - y));
          res.push(x + (nx - x) * t, y + (ny - y) * t);
          return 1;
        }
        if (density(nx, ny) < thr) return 0;
        if (cls === 0 && F.stop > 0 && rnd() < F.stop * ds) return 0;
        const dtest = 0.55 * block * F.block * mul * (fam ? F.aspect : 1);
        if (HF[fam].any(nx, ny, dtest, cls, cls === 1 ? Math.min(dtest, 0.7 * block * F.block) : dtest)) return 0;
        const arc = sgn * k, gap = Math.ceil((3 * dtest) / ds) + 6;
        for (let i = 0; i < ox.length; i++) {
          if (Math.abs(oa[i] - arc) > gap) { const ddx = ox[i] - nx, ddy = oy[i] - ny; if (ddx * ddx + ddy * ddy < dtest * dtest) return 0; }
        }
        ox.push(nx); oy.push(ny); oa.push(arc); res.push(nx, ny);
        x = nx; y = ny; dxp = bx; dyp = by;
      }
      return 0;
    }

    function tryTrace(x, y, fam) {
      if (Math.abs(x) > H || Math.abs(y) > H || density(x, y) < thr) return;
      field(x, y);
      if (F.mag < 0.15) return;
      { const sp = 0.85 * block * F.block * mul * (fam ? F.aspect : 1); if (HF[fam].any(x, y, sp, cls, cls === 1 ? Math.min(sp, 0.9 * block * F.block) : sp)) return; }
      ox.length = 0; oy.length = 0; oa.length = 0;
      ox.push(x); oy.push(y); oa.push(0);
      const fw = [], bw = [];
      const bF = walk(x, y, fam, 1, fw), bB = walk(x, y, fam, -1, bw);
      const pts = [];
      for (let i = bw.length - 2; i >= 0; i -= 2) pts.push(bw[i], bw[i + 1]);
      pts.push(x, y);
      for (let i = 0; i < fw.length; i++) pts.push(fw[i]);
      const n = pts.length / 2;
      if (n * ds < minLen) return;
      const id = lineId++;
      let prev = -1, first = -1;
      for (let i = 0; i < n; i++) {
        const v = addV(pts[2 * i], pts[2 * i + 1], id, i, cls, (i === 0 && bB) || (i === n - 1 && bF));
        HF[fam].add(v);
        if (prev >= 0) addE(prev, v, cls); else first = v;
        prev = v;
      }
      ends.push({ v: first, cls, border: !!bB }, { v: prev, cls, border: !!bF });
      for (let i = 0; i < n; i += stepSeed) {
        const px = pts[2 * i], py = pts[2 * i + 1], j = Math.min(n - 1, i + 1), k = Math.max(0, i - 1);
        let tx = pts[2 * j] - pts[2 * k], ty = pts[2 * j + 1] - pts[2 * k + 1];
        const tl = Math.hypot(tx, ty) || 1; tx /= tl; ty /= tl;
        field(px, py);
        const dsep = block * mul * F.block * (fam ? F.aspect : 1);
        q[fam].push(px - ty * dsep, py + tx * dsep, px + ty * dsep, py - tx * dsep);
        q[1 - fam].push(px, py);
      }
    }

    let ci = 0, turn = 0;
    for (;;) {
      if (vx.length > MAXV) break;
      let fam = -1;
      if (qh[turn] < q[turn].length) fam = turn;
      else if (qh[1 - turn] < q[1 - turn].length) fam = 1 - turn;
      else if (ci < cand.length) { q[0].push(cand[ci], cand[ci + 1]); q[1].push(cand[ci], cand[ci + 1]); ci += 2; continue; }
      else break;
      turn = 1 - fam;
      const x = q[fam][qh[fam]], y = q[fam][qh[fam] + 1];
      qh[fam] += 2;
      tryTrace(x, y, fam);
    }
  }
  if (Math.round(P.collectorEvery) > 0) runPass(1);
  runPass(0);

  // 4. clean-up ───────────────────────────────────────────────────────────────
  // 4a. dead-ends snap to a nearby road (collectors always, locals depending on style/connectivity)
  {
    const all = new Hash(40, vx, vy);
    for (let v = 0; v < vx.length; v++) all.add(v);
    const dg = new Int32Array(vx.length);
    for (let e = 0; e < ea.length; e++) { dg[ea[e]]++; dg[eb[e]]++; }
    for (const e of ends) {
      if (e.border || dg[e.v] !== 1) continue;
      const x = vx[e.v], y = vy[e.v];
      field(x, y);
      const r = e.cls === 1 ? 0.9 * block * Math.max(2, P.collectorEvery) * F.block : 0.75 * block * F.block;
      const lj = vline[e.v], li = vidx[e.v];
      const qv = all.nearest(x, y, r, (id) => id !== e.v && (vline[id] !== lj || Math.abs(vidx[id] - li) > 14) && (e.cls === 0 || vcls[id] >= 1));
      if (qv < 0) continue;
      if (e.cls === 1 || rnd() < clamp(F.join * P.connectivity, 0, 1)) { addE(e.v, qv, e.cls); dg[e.v]++; dg[qv]++; }
    }
  }

  // planarize: split edges where they cross, so every crossing is a real intersection
  function planarize() {
    // drop removed edges + duplicates + loops
    const seenE = new Set(), na = [], nb = [], nc = [];
    for (let e = 0; e < ea.length; e++) {
      if (rem[e] || ea[e] === eb[e]) continue;
      const k = Math.min(ea[e], eb[e]) * 4194304 + Math.max(ea[e], eb[e]);
      if (seenE.has(k)) continue;
      seenE.add(k); na.push(ea[e]); nb.push(eb[e]); nc.push(ec[e]);
    }
    ea = na; eb = nb; ec = nc; rem = new Array(ea.length).fill(0);
    const n = ea.length, cellS = 50, grid = new Map(), stamp = new Int32Array(n).fill(-1), splits = new Map();
    const addSplit = (s, t, v) => { const l = splits.get(s); if (l) l.push([t, v]); else splits.set(s, [[t, v]]); };
    const tol = 1.5;
    for (let i = 0; i < n; i++) {
      const a = ea[i], b = eb[i], ax = vx[a], ay = vy[a], bx = vx[b], by = vy[b];
      const l1 = Math.hypot(bx - ax, by - ay);
      const x0 = Math.floor(Math.min(ax, bx) / cellS), x1 = Math.floor(Math.max(ax, bx) / cellS);
      const y0 = Math.floor(Math.min(ay, by) / cellS), y1 = Math.floor(Math.max(ay, by) / cellS);
      for (let cx = x0; cx <= x1; cx++) for (let cy = y0; cy <= y1; cy++) {
        const k = (cx + 16384) * 32768 + (cy + 16384);
        const list = grid.get(k);
        if (!list) continue;
        for (const j of list) {
          if (stamp[j] === i) continue;
          stamp[j] = i;
          const c = ea[j], d = eb[j];
          if (a === c || a === d || b === c || b === d) continue;
          const cx2 = vx[c], cy2 = vy[c], dx2 = vx[d], dy2 = vy[d];
          const d1x = bx - ax, d1y = by - ay, d2x = dx2 - cx2, d2y = dy2 - cy2, den = d1x * d2y - d1y * d2x;
          if (Math.abs(den) < 1e-9) continue;
          const t = ((cx2 - ax) * d2y - (cy2 - ay) * d2x) / den, u = ((cx2 - ax) * d1y - (cy2 - ay) * d1x) / den;
          if (t <= 0 || t >= 1 || u <= 0 || u >= 1) continue;
          const l2 = Math.hypot(d2x, d2y);
          const nearI = t * l1 < tol ? a : (1 - t) * l1 < tol ? b : -1;
          const nearJ = u * l2 < tol ? c : (1 - u) * l2 < tol ? d : -1;
          if (nearI >= 0 && nearJ >= 0) continue;
          let v;
          if (nearI >= 0) { v = nearI; addSplit(j, u, v); }
          else if (nearJ >= 0) { v = nearJ; addSplit(i, t, v); }
          else {
            v = addV(ax + t * d1x, ay + t * d1y, -2, 0, Math.max(ec[i], ec[j]), 0);
            addSplit(i, t, v); addSplit(j, u, v);
          }
        }
      }
      for (let cx = x0; cx <= x1; cx++) for (let cy = y0; cy <= y1; cy++) {
        const k = (cx + 16384) * 32768 + (cy + 16384);
        const l = grid.get(k);
        if (l) l.push(i); else grid.set(k, [i]);
      }
    }
    if (!splits.size) return;
    const ra = [], rb = [], rc = [];
    for (let e = 0; e < n; e++) {
      const sp = splits.get(e);
      if (!sp) { ra.push(ea[e]); rb.push(eb[e]); rc.push(ec[e]); continue; }
      sp.sort((p, q) => p[0] - q[0]);
      let prev = ea[e];
      for (const [, v] of sp) { if (v !== prev) { ra.push(prev); rb.push(v); rc.push(ec[e]); prev = v; } }
      if (prev !== eb[e]) { ra.push(prev); rb.push(eb[e]); rc.push(ec[e]); }
    }
    ea = ra; eb = rb; ec = rc; rem = new Array(ea.length).fill(0);
  }
  const adjacency = () => {
    const adj = Array.from({ length: vx.length }, () => []);
    for (let e = 0; e < ea.length; e++) if (!rem[e]) { adj[ea[e]].push(e); adj[eb[e]].push(e); }
    return adj;
  };

  // remove very short local stubs left over from snapping
  function pruneSpurs(minLen) {
    const adj = adjacency(), dg = adj.map((a) => a.length);
    for (let v = 0; v < vx.length; v++) {
      if (dg[v] !== 1 || vb[v]) continue;
      let cur = v, prevE = -1, total = 0, ok = false;
      const chain = [];
      for (let guard = 0; guard < 200; guard++) {
        let e = -1;
        for (const x of adj[cur]) if (!rem[x] && x !== prevE) { e = x; break; }
        if (e < 0 || ec[e] > 0) break;
        chain.push(e); total += elen(e);
        const nxt = ea[e] === cur ? eb[e] : ea[e];
        if (total > minLen) break;
        if (dg[nxt] >= 3) { ok = true; break; }
        if (dg[nxt] !== 2) break;
        cur = nxt; prevE = e;
      }
      if (ok) for (const e of chain) { rem[e] = 1; dg[ea[e]]--; dg[eb[e]]--; }
    }
  }

  // a main road may not dead-end: link it to another main road nearby, otherwise demote it
  function fixMain(allowLink) {
    const adj = adjacency();
    const mdeg = new Int32Array(vx.length);
    for (let e = 0; e < ea.length; e++) if (!rem[e] && ec[e] > 0) { mdeg[ea[e]]++; mdeg[eb[e]]++; }
    const stack = [];
    for (let v = 0; v < vx.length; v++) if (mdeg[v] === 1 && !vb[v]) stack.push(v);
    const MH = new Hash(40, vx, vy);
    if (allowLink) for (let v = 0; v < vx.length; v++) if (mdeg[v] > 0) MH.add(v);
    const maxLink = clamp(0.9 * Math.max(2, P.collectorEvery) * block, 3 * block, 1100);
    const nearMain = (v) => {
      const seen = new Set([v]);
      let fr = [v];
      for (let h = 0; h < 12; h++) {
        const nx = [];
        for (const a of fr) for (const e of adj[a]) if (!rem[e] && ec[e] > 0) { const o = ea[e] === a ? eb[e] : ea[e]; if (!seen.has(o)) { seen.add(o); nx.push(o); } }
        fr = nx;
      }
      return seen;
    };
    while (stack.length) {
      const v = stack.pop();
      if (mdeg[v] !== 1 || vb[v]) continue;
      let e = -1;
      for (const x of adj[v]) if (!rem[x] && ec[x] > 0) { e = x; break; }
      if (e < 0) continue;
      if (allowLink) {
        const near = nearMain(v);
        const qv = MH.nearest(vx[v], vy[v], maxLink, (id) => mdeg[id] > 0 && !near.has(id));
        if (qv >= 0) { addE(v, qv, ec[e]); const ne = ea.length - 1; adj[v].push(ne); adj[qv].push(ne); mdeg[v]++; mdeg[qv]++; continue; }
      }
      ec[e]--;
      if (ec[e] > 0) { stack.push(v); continue; }
      const o = ea[e] === v ? eb[e] : ea[e];
      mdeg[v]--; mdeg[o]--;
      if (mdeg[o] === 1 && !vb[o]) stack.push(o);
    }
  }

  // keep one connected city: attach small islands if they are close, drop them otherwise
  function connectComponents() {
    const par = Int32Array.from({ length: vx.length }, (_, k) => k);
    const find = (x) => { while (par[x] !== x) { par[x] = par[par[x]]; x = par[x]; } return x; };
    const used = new Uint8Array(vx.length);
    for (let e = 0; e < ea.length; e++) if (!rem[e]) { par[find(ea[e])] = find(eb[e]); used[ea[e]] = used[eb[e]] = 1; }
    const comps = new Map();
    for (let v = 0; v < vx.length; v++) if (used[v]) { const r = find(v); const l = comps.get(r); if (l) l.push(v); else comps.set(r, [v]); }
    let big = -1, bn = 0;
    for (const [r, l] of comps) if (l.length > bn) { bn = l.length; big = r; }
    const MH = new Hash(60, vx, vy);
    for (const v of comps.get(big) || []) MH.add(v);
    const dropped = new Set();
    for (const [r, l] of comps) {
      if (r === big) continue;
      let bv = -1, bq = -1, bd = Infinity;
      if (l.length >= 12) for (let i = 0; i < l.length; i += 2) {
        const qv = MH.nearest(vx[l[i]], vy[l[i]], 5 * block);
        if (qv >= 0) { const d = Math.hypot(vx[l[i]] - vx[qv], vy[l[i]] - vy[qv]); if (d < bd) { bd = d; bv = l[i]; bq = qv; } }
      }
      if (bv >= 0) { addE(bv, bq, 0); for (const v of l) MH.add(v); } else dropped.add(r);
    }
    if (dropped.size) for (let e = 0; e < ea.length; e++) if (!rem[e] && dropped.has(find(ea[e]))) rem[e] = 1;
  }

  const minSpur = 0.5 * block;
  planarize();
  pruneSpurs(minSpur);
  fixMain(true);
  connectComponents();
  planarize();
  pruneSpurs(minSpur);
  fixMain(false);
  connectComponents();

  // 5. compress chains of plain vertices into polyline edges ──────────────────
  const adj = adjacency(), dg = adj.map((a) => a.length);
  const isKey = new Uint8Array(vx.length);
  for (let v = 0; v < vx.length; v++) {
    if (!dg[v]) continue;
    if (dg[v] !== 2 || vb[v]) isKey[v] = 1;
    else if (ec[adj[v][0]] !== ec[adj[v][1]]) isKey[v] = 1;
  }
  const visited = new Uint8Array(ea.length);
  const nodeIdx = new Int32Array(vx.length).fill(-1);
  const nodes = [], edges = [];
  const nodeOf = (v) => { if (nodeIdx[v] < 0) { nodeIdx[v] = nodes.length; nodes.push({ x: vx[v], y: vy[v], deg: dg[v], border: !!vb[v], main: 0 }); } return nodeIdx[v]; };
  const chain = (sv, e0) => {
    const pts = [vx[sv], vy[sv]];
    let cur = sv, e = e0, len = 0;
    for (;;) {
      visited[e] = 1;
      const nxt = ea[e] === cur ? eb[e] : ea[e];
      pts.push(vx[nxt], vy[nxt]); len += elen(e); cur = nxt;
      if (isKey[cur]) break;
      const ne = adj[cur][0] === e ? adj[cur][1] : adj[cur][0];
      if (visited[ne]) break;
      e = ne;
    }
    edges.push({ a: nodeOf(sv), b: nodeOf(cur), cls: ec[e0], pts, len });
  };
  for (let v = 0; v < vx.length; v++) if (isKey[v]) for (const e of adj[v]) if (!visited[e]) chain(v, e);
  for (let e = 0; e < ea.length; e++) if (!rem[e] && !visited[e]) { isKey[ea[e]] = 1; chain(ea[e], e); }

  if (!edges.length) return { nodes: [], edges: [], districts: [], bounds: { minx: -H, miny: -H, maxx: H, maxy: H }, stats: null, H };

  // stats ─────────────────────────────────────────────────────────────────────
  const km = [0, 0, 0];
  for (const e of edges) {
    km[e.cls] += e.len / 1000;
    if (e.cls > 0) { nodes[e.a].main++; nodes[e.b].main++; }
  }
  let inter = 0, dead = 0, mainDead = 0;
  const built = new Set();
  for (const n of nodes) {
    if (n.deg >= 3) inter++;
    if (n.deg === 1 && !n.border) dead++;
    if (n.main === 1 && !n.border) mainDead++;
    built.add(Math.floor(n.x / 250) * 100000 + Math.floor(n.y / 250));
  }
  const total = km[0] + km[1] + km[2];
  return {
    nodes, edges, H,
    districts: districts.map((d) => ({ x: d.x, y: d.y, r: d.r, style: d.style, a: d.a, pw: d.pw, ex: d.ex })),
    bounds: { minx: -H, miny: -H, maxx: H, maxy: H },
    stats: {
      nodes: nodes.length, km: total, kmMain: km[1] + km[2], kmArterial: km[2], avgEdge: (total * 1000) / edges.length,
      intersections: inter, deadEnds: dead, mainDeadEnds: mainDead, density: total / Math.max(built.size * 0.0625, 1e-6), ms: Date.now() - t0,
    },
  };
}
