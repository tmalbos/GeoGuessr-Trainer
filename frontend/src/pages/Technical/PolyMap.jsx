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

const CLICK_PX = 4; // a press that moves less than this is a click, not a pan

/** features: GeoJSON FeatureCollection | null. fitKey: refit the view to the features when it changes.
 *  A feature with properties.shade > 0 is a rarer part of its tag: drawn lighter. Lines are stroked, points are dots.
 *  onPick(properties | null): called when a shape (or empty map) is clicked. selected: tag to highlight. */
export default function PolyMap({ features, fitKey, onPick, selected = null }) {
  const box = useRef(null);
  const tiles = useRef(null);
  const canvas = useRef(null);
  const tip = useRef(null);
  const api = useRef(null);
  const pickRef = useRef(onPick);
  pickRef.current = onPick;

  useEffect(() => {
    const st = { features: null, hits: [], dpr: 1, selected: null };
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
      const X = (lng) => (fx(lng) - v.cx) * S + size.w / 2;
      const Y = (lat) => (fy(lat) - v.cy) * S + size.h / 2;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, size.w, size.h);
      st.hits = [];
      ctx.lineJoin = "round";
      const top = {}; // rarest shade of each tag
      for (const f of st.features?.features ?? []) top[f.properties.tag] = Math.max(top[f.properties.tag] ?? 0, f.properties.shade ?? 0);
      let chosen = null;
      for (const f of st.features?.features ?? []) {
        const g = f.geometry;
        const path = new Path2D();
        const line = g.type === "LineString" || g.type === "MultiLineString";
        const point = g.type === "Point" || g.type === "MultiPoint";
        if (point) {
          for (const [lng, lat] of g.type === "Point" ? [g.coordinates] : g.coordinates) {
            path.moveTo(X(lng) + 3.5, Y(lat));
            path.arc(X(lng), Y(lat), 3.5, 0, Math.PI * 2);
          }
        } else if (line) {
          for (const pts of g.type === "LineString" ? [g.coordinates] : g.coordinates) {
            pts.forEach(([lng, lat], i) => (i ? path.lineTo(X(lng), Y(lat)) : path.moveTo(X(lng), Y(lat))));
          }
        } else {
          const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
          for (const rings of polys) {
            for (const ring of rings) {
              ring.forEach(([lng, lat], i) => (i ? path.lineTo(X(lng), Y(lat)) : path.moveTo(X(lng), Y(lat))));
              path.closePath();
            }
          }
        }
        const k = shadeFactor(f.properties.shade ?? 0, top[f.properties.tag]);
        if (line) {
          ctx.globalAlpha = 0.9 * k;
          ctx.strokeStyle = f.properties.color;
          ctx.lineWidth = 2;
          ctx.stroke(path);
        } else {
          ctx.globalAlpha = (point ? 0.9 : 0.6) * k;
          ctx.fillStyle = f.properties.color;
          ctx.fill(path, "evenodd");
          ctx.globalAlpha = 0.7 * k;
          ctx.strokeStyle = "#1a1a1a";
          ctx.lineWidth = 1.2;
          ctx.stroke(path);
        }
        if (st.selected && f.properties.tag === st.selected) chosen = path;
        st.hits.push({ path, tag: f.properties.tag, props: f.properties, line });
      }
      if (chosen) { // the selected shape gets a bright outline on top of everything
        ctx.globalAlpha = 1;
        ctx.strokeStyle = "#ffffff";
        ctx.lineWidth = 2.5;
        ctx.stroke(chosen);
      }
    };

    const engine = createMapEngine({ box: box.current, tiles: tiles.current, layer: draw, tileClass: "tp-tile", layerClass: "tp-tl" });

    const el = box.current;
    const hitAt = (x, y) => {
      const ctx = cv.getContext("2d");
      ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
      ctx.lineWidth = 8; // forgiving hit area for lines
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
      pickRef.current?.(hit ? hit.props : null);
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
    api.current.st.features = features;
    api.current.engine.render(true);
  }, [features]);

  useEffect(() => {
    if (!api.current) return;
    api.current.st.selected = selected;
    api.current.engine.render(true);
  }, [selected]);

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
