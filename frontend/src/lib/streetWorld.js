import * as THREE from "three";

// Builds the flat, artificial 3D city from a place (see place.js): ground, water, rails and textured roads.
// three.js axes: x = east, y = up, z = -north. Flat layers have no depth test and are drawn in renderOrder,
// so there is no z-fighting and nothing needs a height offset.

export const HORIZON = 0xcfe3f0;
const ZENITH = 0x4f8fd0;
const LAND = "#6b8a4e";

// width (m), asphalt colour, markings: 0 none, 1 dashed centre, 2 centre + edges, 3 motorway. Index = road class 0..7.
const ROAD = [
  { w: 3, color: "#7a6648", mark: 0 },
  { w: 3.5, color: "#6b6f74", mark: 0 },
  { w: 5, color: "#4e5156", mark: 0 },
  { w: 6, color: "#45484d", mark: 1 },
  { w: 7, color: "#3f4247", mark: 1 },
  { w: 8.5, color: "#3a3d42", mark: 2 },
  { w: 11, color: "#35383d", mark: 2 },
  { w: 14, color: "#303338", mark: 3 },
];
const WATER_LINE_W = [10, 6, 2]; // river, canal, stream
const PERIOD_M = 12; // length of one texture repeat along a road

function canvasTexture(size, draw, aniso) {
  const c = document.createElement("canvas");
  c.width = c.height = size;
  draw(c.getContext("2d"), size);
  const t = new THREE.CanvasTexture(c);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = aniso;
  return t;
}

function speckle(g, S, n, colors, px) {
  for (let i = 0; i < n; i++) {
    g.fillStyle = colors[(Math.random() * colors.length) | 0];
    g.fillRect(Math.random() * S, Math.random() * S, px, px);
  }
}

function roadTexture(spec, aniso) {
  return canvasTexture(256, (g, S) => {
    g.fillStyle = spec.color; g.fillRect(0, 0, S, S);
    speckle(g, S, 2500, ["rgba(255,255,255,.04)", "rgba(0,0,0,.06)"], 2);
    const mx = S / spec.w, my = S / PERIOD_M;
    const line = (xm, wm, color, dashed) => {
      g.fillStyle = color;
      g.fillRect(xm * mx - (wm * mx) / 2, 0, wm * mx, dashed ? 3 * my : S);
    };
    if (spec.mark === 1) line(spec.w / 2, 0.12, "#e8e8e0", true);
    if (spec.mark >= 2) {
      line(0.3, 0.14, "#e8e8e0", false); line(spec.w - 0.3, 0.14, "#e8e8e0", false);
      if (spec.mark === 2) line(spec.w / 2, 0.12, "#e8e8e0", true);
    }
    if (spec.mark === 3) {
      line(spec.w / 2 - 0.15, 0.12, "#e8c547", false); line(spec.w / 2 + 0.15, 0.12, "#e8c547", false);
      line(spec.w / 4, 0.12, "#e8e8e0", true); line((spec.w * 3) / 4, 0.12, "#e8e8e0", true);
    }
  }, aniso);
}

const buf = () => ({ pos: [], uv: [], idx: [] });

/** Flat strip along polyline xy[a..b) with mitred corners. uv: u across (0..1), v along in repeats. */
function ribbon(B, xy, a, b, width) {
  const n = b - a;
  if (n < 2) return;
  const base = B.pos.length / 3;
  const seg = (k) => { // direction of segment k -> k+1, or null
    const dx = xy[2 * (a + k + 1)] - xy[2 * (a + k)], dy = xy[2 * (a + k + 1) + 1] - xy[2 * (a + k) + 1];
    const l = Math.hypot(dx, dy);
    return l > 1e-6 ? [dx / l, dy / l, l] : null;
  };
  let cum = 0;
  for (let k = 0; k < n; k++) {
    const d0 = k > 0 ? seg(k - 1) : null, d1 = k < n - 1 ? seg(k) : null;
    const d = d0 || d1;
    if (k > 0 && d0) cum += d0[2];
    let tx = 0, ty = 1;
    if (d0 && d1) { tx = d0[0] + d1[0]; ty = d0[1] + d1[1]; const l = Math.hypot(tx, ty); if (l < 1e-6) { tx = d1[0]; ty = d1[1]; } else { tx /= l; ty /= l; } }
    else if (d) { tx = d[0]; ty = d[1]; }
    const nxv = -ty, nyv = tx;
    const sd = d1 || d0;
    const dot = sd ? Math.abs(nxv * -sd[1] + nyv * sd[0]) : 1;
    const off = width / 2 / Math.max(0.5, dot);
    const px = xy[2 * (a + k)], py = xy[2 * (a + k) + 1];
    const v = cum / PERIOD_M;
    B.pos.push(px + nxv * off, 0, -(py + nyv * off), px - nxv * off, 0, -(py - nyv * off));
    B.uv.push(0, v, 1, v);
    if (k > 0) { const i = base + 2 * (k - 1); B.idx.push(i, i + 1, i + 2, i + 1, i + 3, i + 2); }
  }
}

/** Fill the polygon parts of a layer (holes ignored: islands are drawn on top instead). */
function polygons(B, L) {
  for (let i = 0; i < L.count; i++) {
    const a = L.start[i];
    let b = L.start[i + 1];
    if (b - a >= 2 && L.xy[2 * a] === L.xy[2 * (b - 1)] && L.xy[2 * a + 1] === L.xy[2 * (b - 1) + 1]) b--;
    if (b - a < 3) continue;
    const ring = [];
    for (let j = a; j < b; j++) ring.push(new THREE.Vector2(L.xy[2 * j], L.xy[2 * j + 1]));
    const base = B.pos.length / 3;
    for (const p of ring) B.pos.push(p.x, 0, -p.y);
    for (const f of THREE.ShapeUtils.triangulateShape(ring, [])) B.idx.push(base + f[0], base + f[1], base + f[2]);
  }
}

function mesh(B, material, order) {
  if (!B.idx.length) return null;
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.Float32BufferAttribute(B.pos, 3));
  if (B.uv.length) g.setAttribute("uv", new THREE.Float32BufferAttribute(B.uv, 2));
  g.setIndex(new THREE.BufferAttribute(B.idx.length > 65535 ? Uint32Array.from(B.idx) : Uint16Array.from(B.idx), 1));
  const m = new THREE.Mesh(g, material);
  m.renderOrder = order;
  m.frustumCulled = false;
  return m;
}

const flat = (props) => new THREE.MeshBasicMaterial({ side: THREE.DoubleSide, depthTest: false, depthWrite: false, ...props });

export function buildWorld(place, aniso = 4) {
  const { layers: Ly, meta } = place;
  const group = new THREE.Group();
  const add = (m) => m && group.add(m);

  // Ground (tiled speckle so you can feel the motion on empty land)
  const size = meta.half_m * 2 + 4000;
  const gt = canvasTexture(256, (g, S) => {
    g.fillStyle = LAND; g.fillRect(0, 0, S, S);
    speckle(g, S, 5000, ["#5f7d44", "#779757", "#6a8a4a", "#587540"], 3);
  }, aniso);
  gt.repeat.set(size / 10, size / 10);
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(size, size), flat({ map: gt }));
  ground.rotation.x = -Math.PI / 2;
  ground.renderOrder = 0;
  group.add(ground);

  // Water
  const sea = buf(); polygons(sea, Ly.sea); add(mesh(sea, flat({ color: "#2b6f94" }), 1));
  const isl = buf(); polygons(isl, Ly.islands); add(mesh(isl, flat({ color: LAND }), 2));
  const lake = buf(); polygons(lake, Ly.water_areas); add(mesh(lake, flat({ color: "#3a86ad" }), 3));
  const W = Ly.water_lines;
  const rivers = buf();
  for (let i = 0; i < W.count; i++) ribbon(rivers, W.xy, W.start[i], W.start[i + 1], WATER_LINE_W[Math.min(2, W.kind[i])]);
  add(mesh(rivers, flat({ color: "#3a86ad" }), 4));

  // Rail
  const T = Ly.rail, rail = buf();
  for (let i = 0; i < T.count; i++) ribbon(rail, T.xy, T.start[i], T.start[i + 1], 2.6);
  add(mesh(rail, flat({ color: "#6b655d" }), 5));

  // Roads: one mesh per (class, bridge) so every group shares a texture. Tunnels are skipped.
  const R = Ly.roads, groups = new Map();
  for (let i = 0; i < R.count; i++) {
    if (R.flag[i] & 2) continue;
    const bridge = R.flag[i] & 1 ? 1 : 0, k = bridge * 8 + R.cls[i];
    if (!groups.has(k)) groups.set(k, buf());
    ribbon(groups.get(k), R.xy, R.start[i], R.start[i + 1], ROAD[R.cls[i]].w);
  }
  for (const [k, B] of groups) {
    const cls = k % 8;
    add(mesh(B, flat({ map: roadTexture(ROAD[cls], aniso) }), 10 + (k >= 8 ? 20 : 0) + cls));
  }

  // Sky dome (follows the camera)
  const sg = new THREE.SphereGeometry(2500, 24, 16);
  const horizon = new THREE.Color(HORIZON), zenith = new THREE.Color(ZENITH), col = [];
  const p = sg.attributes.position;
  for (let i = 0; i < p.count; i++) {
    const t = Math.pow(Math.max(0, p.getY(i) / 2500), 0.6);
    const c = horizon.clone().lerp(zenith, t);
    col.push(c.r, c.g, c.b);
  }
  sg.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  const sky = new THREE.Mesh(sg, new THREE.MeshBasicMaterial({ vertexColors: true, side: THREE.BackSide, fog: false, depthWrite: false, depthTest: false }));
  sky.renderOrder = -1;

  const dispose = () => {
    for (const root of [group, sky]) root.traverse((o) => {
      o.geometry?.dispose();
      if (o.material) { o.material.map?.dispose(); o.material.dispose(); }
    });
  };
  return { group, sky, dispose };
}
