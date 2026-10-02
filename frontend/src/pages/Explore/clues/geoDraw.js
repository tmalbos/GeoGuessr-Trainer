// Turns GeoJSON (lon/lat) into SVG path data. No dependencies.
const f = (n) => n.toFixed(1);

function walk(g, fn) {
  if (!g) return;
  switch (g.type) {
    case "GeometryCollection": g.geometries.forEach((s) => walk(s, fn)); break;
    case "Point": fn(g.coordinates[0], g.coordinates[1]); break;
    case "MultiPoint": case "LineString": g.coordinates.forEach((c) => fn(c[0], c[1])); break;
    case "MultiLineString": case "Polygon": g.coordinates.forEach((r) => r.forEach((c) => fn(c[0], c[1]))); break;
    case "MultiPolygon": g.coordinates.forEach((p) => p.forEach((r) => r.forEach((c) => fn(c[0], c[1])))); break;
    default:
  }
}

export function bboxOf(gj) {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  walk(gj, (x, y) => { x0 = Math.min(x0, x); x1 = Math.max(x1, x); y0 = Math.min(y0, y); y1 = Math.max(y1, y); });
  return x0 === Infinity ? null : [x0, y0, x1, y1];
}

/** Fit a lon/lat bbox into W×H, keeping proportions (used for standalone thumbnails). */
export function fitProjection(bbox, W, H, pad = 6) {
  const [x0, y0, x1, y1] = bbox;
  const k = Math.cos((((y0 + y1) / 2) * Math.PI) / 180) || 1;
  const w = Math.max((x1 - x0) * k, 1e-6), h = Math.max(y1 - y0, 1e-6);
  const s = Math.min((W - 2 * pad) / w, (H - 2 * pad) / h);
  const ox = (W - w * s) / 2, oy = (H - h * s) / 2;
  return ([lon, lat]) => [ox + (lon - x0) * k * s, oy + (y1 - lat) * s];
}

/** Linear lon/lat -> map units, for a map that was fitted to `bbox` and is w×h units (see ClueMap). */
export function alignedProjection(bbox, w, h) {
  const [x0, y0, x1, y1] = bbox;
  const sx = w / (x1 - x0), sy = h / (y1 - y0);
  return ([lon, lat]) => [(lon - x0) * sx, (y1 - lat) * sy];
}

export function shapes(gj, project) {
  const out = { polys: "", lines: "", pts: [] };
  const ring = (r, close) => r.map((c, i) => { const [x, y] = project(c); return `${i ? "L" : "M"}${f(x)} ${f(y)}`; }).join("") + (close ? "Z" : "");
  const add = (g) => {
    switch (g.type) {
      case "GeometryCollection": g.geometries.forEach(add); break;
      case "Point": out.pts.push(project(g.coordinates)); break;
      case "MultiPoint": g.coordinates.forEach((c) => out.pts.push(project(c))); break;
      case "LineString": out.lines += ring(g.coordinates, false); break;
      case "MultiLineString": g.coordinates.forEach((l) => { out.lines += ring(l, false); }); break;
      case "Polygon": g.coordinates.forEach((r) => { out.polys += ring(r, true); }); break;
      case "MultiPolygon": g.coordinates.forEach((p) => p.forEach((r) => { out.polys += ring(r, true); })); break;
      default:
    }
  };
  add(gj);
  return out;
}
