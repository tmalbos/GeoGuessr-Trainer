import * as THREE from "three";
import { buildRoadGraph, pickStart } from "./roadGraph.js";
import { HORIZON, buildWorld } from "./streetWorld.js";

// First-person walk through a place, Street View style:
//  - you stand on a panorama node 2.5 m above the road, drag to look around, scroll to zoom
//  - ground arrows mark the links; click one (or press W / S) to glide to the next node
//  - after a move the arrow that continues in the same direction is selected; hold Space to keep going that way
//  - A / D (or left / right) turn the view

const EYE = 2.5;
const ARROW_DIST = 4.5;
const MOVE_MS = 420, FAST_MS = 240; // normal step / Space-repeat step
const TURN_RATE = 1.6; // rad/s with the keyboard
const MAX_PITCH = THREE.MathUtils.degToRad(85);
const FWD_CONE = 1.2; // rad (~70 deg): how far off a direction a link can be and still count as "that way"
const TAU = Math.PI * 2;

const wrapPi = (a) => { a = (a + Math.PI) % TAU; if (a < 0) a += TAU; return a - Math.PI; };
const bearing = (x0, y0, x1, y1) => Math.atan2(x1 - x0, y1 - y0); // compass: 0 = north, clockwise
const ease = (t) => t * t * (3 - 2 * t);
const KEYS = { w: "f", arrowup: "f", s: "b", arrowdown: "b", a: "l", arrowleft: "l", d: "r", arrowright: "r", " ": "sp" };

// GeoGuessr's arrow outline: M 0 -120 L 60 -60 L 40 -30 L -40 -30 L -60 -60 (SVG units, tip up).
// Flipped to ground axes and recentred; 40 units = 1 m.
const ARROW = [[0, 120], [60, 60], [40, 30], [-40, 30], [-60, 60]];
function arrowGeometry() {
  const s = new THREE.Shape();
  ARROW.forEach(([x, y], i) => (i ? s.lineTo(x / 40, (y - 75) / 40) : s.moveTo(x / 40, (y - 75) / 40)));
  s.closePath();
  return new THREE.ShapeGeometry(s);
}

export function createStreetView(container, place, { onView, onNode } = {}) {
  const renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  const canvas = renderer.domElement;
  canvas.style.cssText = "position:absolute;inset:0;width:100%;height:100%;display:block;touch-action:none;cursor:grab";
  container.appendChild(canvas);

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(HORIZON);
  scene.fog = new THREE.Fog(HORIZON, 120, 1100);
  const camera = new THREE.PerspectiveCamera(70, 1, 0.3, 3000);
  camera.rotation.order = "YXZ";

  const world = buildWorld(place, renderer.capabilities.getMaxAnisotropy());
  scene.add(world.group, world.sky);
  const graph = buildRoadGraph(place);

  // ── state ──
  let node = graph.count ? pickStart(graph) : -1;
  const startNode = node;
  let heading = 0, pitch = 0, hfov = 90, steps = 0, anim = null, hovered = null;
  let travel = null; // bearing of the last move
  let sel = null;    // selected arrow (link node), the one that keeps the travel direction
  const keys = new Set();
  const here = () => (node < 0 ? { x: 0, y: 0 } : { x: graph.x[node], y: graph.y[node] });
  const bearingTo = (a, b) => bearing(graph.x[a], graph.y[a], graph.x[b], graph.y[b]);
  if (node >= 0 && graph.adj[node].length) heading = bearingTo(node, graph.adj[node][0]);
  const start = here();

  // ── ground arrows ──
  const arrowGeo = arrowGeometry();
  const mat = (color, opacity) => new THREE.MeshBasicMaterial({ color, transparent: true, opacity, side: THREE.DoubleSide, depthTest: false, depthWrite: false, fog: false });
  const darkMat = mat(0x000000, 0.4), idleMat = mat(0xffffff, 0.75), hoverMat = mat(0xffffff, 1);
  const arrows = new THREE.Group();
  scene.add(arrows);
  let hits = [];
  const rest = (m) => (m.userData.nb === sel ? hoverMat : idleMat);

  function rebuildArrows() {
    arrows.clear();
    hits = []; hovered = null; sel = null;
    if (node < 0) return;
    arrows.position.set(graph.x[node], 0.05, -graph.y[node]);
    const shown = [];
    for (const nb of graph.adj[node]) {
      const b = bearingTo(node, nb);
      if (shown.some((s) => Math.abs(wrapPi(s.b - b)) < 0.17)) continue; // two links in nearly the same direction: show one
      shown.push({ nb, b });
    }
    if (travel != null) {
      let bd = FWD_CONE;
      for (const s of shown) { const d = Math.abs(wrapPi(s.b - travel)); if (d < bd) { bd = d; sel = s.nb; } }
    }
    for (const { nb, b } of shown) {
      const pivot = new THREE.Object3D();
      pivot.rotation.y = -b;
      const dark = new THREE.Mesh(arrowGeo, darkMat), light = new THREE.Mesh(arrowGeo, nb === sel ? hoverMat : idleMat);
      dark.scale.setScalar(1.25);
      for (const m of [dark, light]) { m.rotation.x = -Math.PI / 2; m.position.z = -ARROW_DIST; m.userData = { nb, light }; }
      dark.renderOrder = 90; light.renderOrder = 91;
      pivot.add(dark, light);
      arrows.add(pivot);
      hits.push(dark);
    }
  }
  const info = () => {
    const p = here();
    return { steps, x: p.x, y: p.y, nodes: graph.count };
  };

  // ── movement ──
  function go(to, turn, fast = false) {
    travel = bearingTo(node, to);
    anim = { from: node, to, t: 0, dur: fast ? FAST_MS : MOVE_MS, h0: heading, h1: turn ? heading + wrapPi(travel - heading) : heading };
    arrows.visible = false;
  }
  function finish() {
    node = anim.to; heading = anim.h1; anim = null; steps++;
    arrows.visible = true;
    rebuildArrows();
    onNode?.(info());
  }
  function teleport(n) { // instant jump (return to start / checkpoint); not counted as a step
    if (n == null || n < 0 || n >= graph.count) return;
    anim = null; arrows.visible = true; node = n; pitch = 0; travel = null;
    if (graph.adj[n].length) heading = bearingTo(n, graph.adj[n][0]);
    rebuildArrows();
    onNode?.(info());
  }
  function pick(h) { // link whose bearing is closest to h, within the cone
    let best = -1, bd = FWD_CONE;
    for (const nb of graph.adj[node]) {
      const d = Math.abs(wrapPi(bearingTo(node, nb) - h));
      if (d < bd) { bd = d; best = nb; }
    }
    return best;
  }

  // ── input ──
  const ray = new THREE.Raycaster(), ndc = new THREE.Vector2();
  const arrowAt = (e) => {
    const r = canvas.getBoundingClientRect();
    ndc.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
    ray.setFromCamera(ndc, camera);
    return arrows.visible ? ray.intersectObjects(hits, false)[0]?.object ?? null : null;
  };
  const setHover = (m) => {
    if (m === hovered) return;
    if (hovered) hovered.userData.light.material = rest(hovered);
    hovered = m;
    if (m) m.userData.light.material = hoverMat;
    canvas.style.cursor = m ? "pointer" : "grab";
  };

  let drag = null;
  const vfovRad = () => THREE.MathUtils.degToRad(camera.fov);
  const onDown = (e) => { canvas.setPointerCapture(e.pointerId); drag = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY }; canvas.style.cursor = "grabbing"; };
  const onMove = (e) => {
    if (!drag) return setHover(arrowAt(e));
    const w = canvas.clientWidth, h = canvas.clientHeight;
    const dh = -((e.clientX - drag.x) / w) * THREE.MathUtils.degToRad(hfov);
    heading += dh;
    if (anim) { anim.h0 += dh; anim.h1 += dh; }
    pitch = Math.max(-MAX_PITCH, Math.min(MAX_PITCH, pitch + ((e.clientY - drag.y) / h) * vfovRad()));
    drag.x = e.clientX; drag.y = e.clientY;
  };
  const onUp = (e) => {
    if (!drag) return;
    const moved = Math.hypot(e.clientX - drag.sx, e.clientY - drag.sy) > 5;
    drag = null;
    const m = !moved && !anim ? arrowAt(e) : null;
    if (m) go(m.userData.nb, true);
    canvas.style.cursor = m ? "grab" : hovered ? "pointer" : "grab";
  };
  const onWheel = (e) => {
    e.preventDefault();
    hfov = Math.max(30, Math.min(100, hfov * Math.exp(e.deltaY * 0.001)));
    applyFov();
  };
  const typing = (e) => /^(INPUT|SELECT|TEXTAREA)$/.test(e.target?.tagName ?? "");
  const onKeyDown = (e) => {
    const k = KEYS[e.key.toLowerCase()];
    if (!k || typing(e) || e.ctrlKey || e.metaKey || e.altKey) return;
    if (k === "sp" && e.target?.tagName === "BUTTON") return; // let Space press a focused button
    keys.add(k); e.preventDefault();
  };
  const onKeyUp = (e) => { const k = KEYS[e.key.toLowerCase()]; if (k) keys.delete(k); };
  const onBlur = () => keys.clear();

  canvas.addEventListener("pointerdown", onDown);
  canvas.addEventListener("pointermove", onMove);
  canvas.addEventListener("pointerup", onUp);
  canvas.addEventListener("pointercancel", () => { drag = null; });
  canvas.addEventListener("wheel", onWheel, { passive: false });
  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);
  window.addEventListener("blur", onBlur);

  // ── sizing ──
  function applyFov() {
    const v = 2 * Math.atan(Math.tan(THREE.MathUtils.degToRad(hfov) / 2) / camera.aspect);
    camera.fov = Math.min(100, THREE.MathUtils.radToDeg(v));
    camera.updateProjectionMatrix();
  }
  const resize = () => {
    const w = container.clientWidth || 1, h = container.clientHeight || 1;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    applyFov();
  };
  const ro = new ResizeObserver(resize);
  ro.observe(container);
  resize();
  rebuildArrows();
  onNode?.(info());

  // ── loop ──
  let last = performance.now(), raf = 0;
  const frame = (now) => {
    raf = requestAnimationFrame(frame);
    const dt = Math.min(0.1, (now - last) / 1000);
    last = now;

    if (anim) {
      anim.t += (dt * 1000) / anim.dur;
      if (anim.t >= 1) finish();
    }
    if (!anim && node >= 0) {
      heading += (keys.has("r") - keys.has("l")) * TURN_RATE * (hfov / 90) * dt;
      if (keys.has("f")) { const t = pick(heading); if (t >= 0) go(t, true); }
      else if (keys.has("b")) { const t = pick(heading + Math.PI); if (t >= 0) go(t, false); }
      else if (keys.has("sp") && sel != null) go(sel, false, true); // keep going the way you were going
    }

    let px, py;
    if (anim) {
      const e = ease(anim.t);
      px = graph.x[anim.from] + (graph.x[anim.to] - graph.x[anim.from]) * e;
      py = graph.y[anim.from] + (graph.y[anim.to] - graph.y[anim.from]) * e;
      heading = anim.h0 + (anim.h1 - anim.h0) * e;
    } else ({ x: px, y: py } = here());

    camera.position.set(px, EYE, -py);
    camera.rotation.set(pitch, -heading, 0);
    world.sky.position.copy(camera.position);
    renderer.render(scene, camera);
    onView?.(((heading % TAU) + TAU) % TAU);
  };
  raf = requestAnimationFrame(frame);

  return {
    empty: node < 0,
    nodes: graph.count,
    start,            // where the round began = the location to guess ({x, y} in metres)
    startNode,
    current: () => node,
    teleport,
    dispose() {
      cancelAnimationFrame(raf);
      ro.disconnect();
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
      window.removeEventListener("blur", onBlur);
      world.dispose();
      arrowGeo.dispose(); darkMat.dispose(); idleMat.dispose(); hoverMat.dispose();
      renderer.dispose();
      canvas.remove();
    },
  };
}
