import { useEffect, useRef } from "react";
import { TILE, createMapEngine, fx, fy } from "../../lib/googleMap.js";
import { attribution } from "../../lib/mapProviders.js";

// Polygons + places drawn on one canvas, redrawn on every frame of the shared map engine (lib/googleMap.js).

function featureBounds(fc) {
  let w = Infinity, s = Infinity, e = -Infinity, n = -Infinity;
  const walk = (c) => {
    if (typeof c[0] === "number") { w = Math.min(w, c[0]); e = Math.max(e, c[0]); s = Math.min(s, c[1]); n = Math.max(n, c[1]); }
    else c.forEach(walk);
  };
  fc?.features.forEach((f) => walk(f.geometry.coordinates));
  return Number.isFinite(w) ? { w, s, e, n } : null;
}

/** features: GeoJSON FeatureCollection | null. pts: [{ lat, lng, color }]. fitKey: refit the view when it changes. */
export default function PolyMap({ features, pts, basemap, fitKey }) {
  const box = useRef(null);
  const tiles = useRef(null);
  const canvas = useRef(null);
  const tip = useRef(null);
  const api = useRef(null);

  useEffect(() => {
    const st = { features: null, pts: [], hits: [], dpr: 1 };
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
      for (const f of st.features?.features ?? []) {
        const g = f.geometry;
        const polys = g.type === "Polygon" ? [g.coordinates] : g.coordinates;
        const path = new Path2D();
        for (const rings of polys) {
          for (const ring of rings) {
            ring.forEach(([lng, lat], i) => (i ? path.lineTo(X(lng), Y(lat)) : path.moveTo(X(lng), Y(lat))));
            path.closePath();
          }
        }
        ctx.globalAlpha = 0.6;
        ctx.fillStyle = f.properties.color;
        ctx.fill(path, "evenodd");
        ctx.globalAlpha = 0.7;
        ctx.strokeStyle = "#1a1a1a";
        ctx.lineWidth = 1.2;
        ctx.stroke(path);
        st.hits.push({ path, tag: f.properties.tag });
      }
      ctx.globalAlpha = 1;
      ctx.lineWidth = 0.8;
      ctx.strokeStyle = "rgba(0,0,0,.7)";
      for (const p of st.pts) {
        const x = X(p.lng), y = Y(p.lat);
        if (x < -5 || y < -5 || x > size.w + 5 || y > size.h + 5) continue;
        ctx.beginPath();
        ctx.arc(x, y, 2.5, 0, 2 * Math.PI);
        ctx.fillStyle = p.color;
        ctx.fill();
        ctx.stroke();
      }
    };

    const engine = createMapEngine({ box: box.current, tiles: tiles.current, layer: draw, tileClass: "tp-tile", layerClass: "tp-tl" });

    // Tooltip with the tag name under the cursor.
    const el = box.current;
    const hide = () => { tip.current.hidden = true; };
    const onHover = (e) => {
      if (el.classList.contains("moving")) return hide();
      const r = el.getBoundingClientRect();
      const x = e.clientX - r.left, y = e.clientY - r.top;
      const ctx = cv.getContext("2d");
      ctx.setTransform(st.dpr, 0, 0, st.dpr, 0, 0);
      let hit = null;
      for (let i = st.hits.length - 1; i >= 0 && !hit; i--) if (ctx.isPointInPath(st.hits[i].path, x * st.dpr, y * st.dpr, "evenodd")) hit = st.hits[i];
      if (!hit) return hide();
      tip.current.hidden = false;
      tip.current.textContent = hit.tag;
      tip.current.style.left = `${x + 12}px`;
      tip.current.style.top = `${y + 12}px`;
    };
    el.addEventListener("pointermove", onHover);
    el.addEventListener("pointerleave", hide);

    api.current = { engine, st };
    return () => {
      el.removeEventListener("pointermove", onHover);
      el.removeEventListener("pointerleave", hide);
      engine.destroy();
      api.current = null;
    };
  }, []);

  useEffect(() => { api.current?.engine.setTilesVisible(basemap); }, [basemap]);

  useEffect(() => {
    if (!api.current) return;
    api.current.st.features = features;
    api.current.st.pts = pts;
    api.current.engine.render(true);
  }, [features, pts]);

  // Fit to the places as soon as the selection changes (they are known instantly; polygons follow).
  const latest = useRef({});
  latest.current = { features, pts };
  useEffect(() => {
    if (!api.current) return;
    const { features: f, pts: p } = latest.current;
    const b = p.length
      ? p.reduce((a, q) => ({ w: Math.min(a.w, q.lng), e: Math.max(a.e, q.lng), s: Math.min(a.s, q.lat), n: Math.max(a.n, q.lat) }), { w: Infinity, e: -Infinity, s: Infinity, n: -Infinity })
      : featureBounds(f);
    if (b) api.current.engine.fit(b, 30, 10, true);
  }, [fitKey]);

  return <div ref={box} className="tp-map" role="img" aria-label="Map with the selected places and their polygons">
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
