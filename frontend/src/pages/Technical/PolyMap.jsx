import { useEffect, useRef } from "react";
import { TILE, createMapEngine, fx, fy } from "../../lib/googleMap.js";
import { attribution } from "../../lib/mapProviders.js";
import { shadeFactor } from "../Explore/clues/geoDraw.js";

// Polygons, lines + places drawn on one canvas, redrawn on every frame of the shared map engine (lib/googleMap.js).

function featureBounds(fc) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number") { w = Math.min(w, c[0]); e = Math.max(e, c[0]); s = Math.min(s, c[1]); n = Math.max(n, c[1]); }
    else c.forEach(walk);
  };
  fc?.features.forEach((f) => walk(f.geometry.coordinates));
  return Number.isFinite(w) ? { w, s, e, n } : null;
}

/** Path2Ds in normalized mercator space (fx / fy), built once per feature set. The view is applied as a canvas transform. */
function buildShapes(fc) {
  const top = {}; // rarest shade of each tag
  for (const f of fc?.features ?? []) top[f.properties.tag] = Math.max(top[f.properties.tag] ?? 0, f.properties.shade ?? 0);
  return (fc?.features ?? []).map((f) => {
    const g = f.geometry;
    const path = new Path2D();
    const line = g.type === "LineString" || g.type === "MultiLineString";
    const point = g.type === "Point" || g.type === "MultiPoint";
    const box = [Infinity, Infinity, -Infinity, -Infinity];
    const to = ([lng, lat]) => {
      const x = fx(lng), y = fy(lat);
      box[0] = Math.min(box[0], x); box[1] = Math.min(box[1], y); box[2] = Math.max(box[2], x); box[3] = Math.max(box[3], y);
      return [x, y];
    };
    const trace = (pts) => pts.forEach((c, i) => { const [x, y] = to(c); if (i) path.lineTo(x, y); else path.moveTo(x, y); });
    if (point) {
      for (const c of g.type === "Point" ? [g.coordinates] : g.coordinates) { const [x, y] = to(c); path.moveTo(x, y); path.lineTo(x, y); }
    } else if (line) {
      for (const pts of g.type === "LineString" ? [g.coordinates] : g.coordinates) trace(pts);
    } else {
      for (const rings of g.type === "Polygon" ? [g.coordinates] : g.coordinates) for (const ring of rings) { trace(ring); path.closePath(); }
    }
    return { path, line, point, box, props: f.properties, tag: f.properties.tag, k: shadeFactor(f.properties.shade ?? 0, top[f.properties.tag]) };
  });
}

const CLICK_PX = 4; // a press that moves less than this is a click, not a pan

/** features: GeoJSON FeatureCollection | null. fitKey: refit the view to the features when it changes.
 *  A feature with properties.shade > 0 is a rarer part of its tag: drawn lighter. Lines are stroked, points are dots.
 *  onPick(properties | null): called when a shape (or empty map) is clicked. selected: tag to highlight. */
export default function PolyMap({ features, fitKey, onPick, selected = null, marked = null }) {
  const box = useRef(null);
  const tiles = useRef(null);
  const canvas = useRef(null);
  const tip = useRef(null);
  const api = useRef(null);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    const st = { shapes: [], hits: [], dpr: 1, xf: { S: 1, ox: 0, oy: 0 }, selected: null, marked: null };
    const cv = canvas.current;

    const draw = (v, size) => {
      const dpr = window.devicePixelRatio || 1;
      if (cv.width !== Math.round(size.w * dpr) || cv.height !== Math.round(size.h * dpr)) {
        cv.width = Math.round(size.w * dpr);
        cv.height = Math.round(size.h * dpr);
      }
      st.dpr = dpr;
      const ctx = cv.getContext("2d");
      const S = TILE * 2 ** v.z;
      const ox = size.w / 2 - v.cx * S;
      const oy = size.h / 2 - v.cy * S;
      st.xf = { S, ox, oy };
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.w, size.h);
      ctx.setTransform(dpr * S, 0, 0, dpr * S, dpr * ox, dpr * oy); // paths are in normalized mercator: the view is just this transform
      const px = 1 / S; // one screen pixel, in path units
      st.hits = [];
      ctx.lineJoin = "round";
      let chosen = null;
      const extra = [];
      for (const sh of st.shapes) {
        const { path, line, point, props } = sh;
        const x0 = sh.box[0] * S + ox, x1 = sh.box[2] * S + ox, y0 = sh.box[1] * S + oy, y1 = sh.box[3] * S + oy;
        if (x1 < -8 || x0 > size.w + 8 || y1 < -8 || y0 > size.h + 8) continue; // off screen
        if (x1 - x0 < 1 && y1 - y0 < 1 && !point) { // under 1 px: a dot instead of a path
          ctx.globalAlpha = 0.8 * sh.k;
          ctx.fillStyle = props.color;
          ctx.fillRect(sh.box[0], sh.box[1], 1.5 * px, 1.5 * px);
          continue;
        }
        if (line) {
          ctx.globalAlpha = 0.9 * sh.k;
          ctx.strokeStyle = props.color;
          ctx.lineWidth = 2 * px;
          ctx.stroke(path);
        } else if (point) { // a zero-length segment with a round cap is a dot that keeps its size on screen
          ctx.lineCap = "round";
          ctx.globalAlpha = 0.7 * sh.k;
          ctx.strokeStyle = "#1a1a1a";
          ctx.lineWidth = 9.4 * px;
          ctx.stroke(path);
          ctx.globalAlpha = 0.9 * sh.k;
          ctx.strokeStyle = props.color;
          ctx.lineWidth = 7 * px;
          ctx.stroke(path);
          ctx.lineCap = "butt";
        } else {
          ctx.globalAlpha = 0.6 * sh.k;
          ctx.fillStyle = props.color;
          ctx.fill(path, "evenodd");
          ctx.globalAlpha = 0.7 * sh.k;
          ctx.strokeStyle = "#1a1a1a";
          ctx.lineWidth = 1.2 * px;
          ctx.stroke(path);
        }
        if (st.selected && sh.tag === st.selected) chosen = path;
        if (st.marked?.some((m) => m.admin_level === props.admin_level && m.idx === props.idx)) extra.push(path);
        st.hits.push(sh);
      }
      if (chosen) extra.push(chosen);
      if (extra.length) { // the selected shapes get a bright outline on top of everything
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2.5 * px;
        for (const q of extra) ctx.stroke(q);
      }
    };

    const engine = createMapEngine({ box: box.current, tiles: tiles.current, layer: draw, tileClass: "tp-tile", layerClass: "tp-tl" });

    const el = box.current;
    const hitAt = (x, y) => {
      const ctx = cv.getContext("2d");
      const { S, ox, oy } = st.xf;
      ctx.setTransform(st.dpr * S, 0, 0, st.dpr * S, st.dpr * ox, st.dpr * oy);
      ctx.lineWidth = 8 / S; // forgiving hit area for lines
      for (let i = st.hits.length - 1; i >= 0; i--) {
        const h = st.hits[i];
        if (h.line ? ctx.isPointInStroke(h.path, x * st.dpr, y * st.dpr) : ctx.isPointInPath(h.path, x * st.dpr, y * st.dpr, "evenodd")) return h;
      }
      return null;
    };

    // Tooltip with the tag name under the cursor.
    const hide = () => { tip.current.hidden = true; };
    const onHover = (e) => {
      if (el.classList.contains("moving")) return hide();
      const r = el.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      const hit = hitAt(x, y);
      if (!hit) return hide();
      tip.current.hidden = false;
      tip.current.textContent = hit.tag;
      tip.current.style.left = `${x + 12}px`;
      tip.current.style.top = `${y + 12}px`;
    };

    // Click (a press that did not move) picks the shape under the cursor.
    let down = null;
    const onDown = (e) => { down = e.target.closest?.("button") ? null : { x: e.clientX, y: e.clientY }; };
    const onUp = (e) => {
      const d = down;
      down = null;
      if (!d || Math.hypot(e.clientX - d.x, e.clientY - d.y) > CLICK_PX) return;
      const r = el.getBoundingClientRect();
      const hit = hitAt(e.clientX - r.left, e.clientY - r.top);
      pickRef.current?.(hit ? hit.props : null, e.shiftKey);
    };

    el.addEventListener("pointermove", onHover);
    el.addEventListener("pointerleave", hide);
    el.addEventListener("pointerdown", onDown);
    el.addEventListener("pointerup", onUp);

    api.current = { engine, st };
    return () => {
      el.removeEventListener("pointermove", onHover);
      el.removeEventListener("pointerleave", hide);
      el.removeEventListener("pointerdown", onDown);
      el.removeEventListener("pointerup", onUp);
      engine.destroy();
      api.current = null;
    };
  }, []);

  useEffect(() => {
    if (!api.current) return;
    const st = api.current.st;
    st.shapes = buildShapes(features);
    api.current.engine.render(true);
  }, [features]);

  useEffect(() => {
    if (!api.current) return;
    api.current.st.selected = selected;
    api.current.engine.render(true);
  }, [selected]);

  useEffect(() => {
    if (!api.current) return;
    api.current.st.marked = marked;
    api.current.engine.render(true);
  }, [marked]);

  // Fit to the features when fitKey changes (the tools change it when the result for a new selection arrives).
  const latest = useRef({});
  latest.current = { features };
  useEffect(() => {
    if (!api.current) return;
    const b = featureBounds(latest.current.features);
    if (b) api.current.engine.fit(b, 30, 10, true);
  }, [fitKey]);

  return <div ref={box} className="tp-map" role="img" aria-label="Map with the features">
    <div ref={tiles} className="tp-tiles" />
    <canvas ref={canvas} className="tp-canvas" />
    <div className="tp-zoom">
      <button type="button" className="btn" aria-label="Zoom in" onClick={() => api.current?.engine.zoomBy(1)}>+</button>
      <button type="button" className="btn" aria-label="Zoom out" onClick={() => api.current?.engine.zoomBy(-1)}>−</button>
    </div>
    <div className="tp-attr">{attribution()}</div>
    <div ref={tip} className="tp-tip" hidden />
  </div>;
}
