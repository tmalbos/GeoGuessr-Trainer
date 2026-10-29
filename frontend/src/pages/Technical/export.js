import { bboxOf, fitProjection, shadeFactor, shapes } from "../Explore/clues/geoDraw.js";

export function download(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}

export const toGeoJSON = (fc) => JSON.stringify(fc);

/** Valid TopoJSON without shared-arc dedupe or quantization: one object per tag, every ring is its own arc. */
export function toTopoJSON(fc) {
  const arcs = [];
  const ring = (r) => arcs.push(r) - 1;
  const poly = (rings) => rings.map(ring);
  const objects = {};
  for (const f of fc.features) {
    const g = f.geometry;
    const geom = g.type === "Polygon" ? { type: "Polygon", arcs: poly(g.coordinates) } : { type: "MultiPolygon", arcs: g.coordinates.map(poly) };
    (objects[f.properties.tag] ??= { type: "GeometryCollection", geometries: [] }).geometries.push({ ...geom, properties: { ...f.properties } });
  }
  return JSON.stringify({ type: "Topology", objects, arcs });
}

const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

/** SVG of the polygons on top of `base` (a country outline geometry) with its division `borders` (lines) over them, equirectangular, fitted to both. Returns { svg, w, h }. */
export function toSVG(fc, { width = 1600, background = null, base = null, borders = null } = {}) {
  const geoms = fc.features.map((f) => f.geometry);
  const bb = bboxOf({ type: "GeometryCollection", geometries: base ? [base, ...geoms] : geoms });
  if (!bb) return null;
  const k = Math.cos((((bb[1] + bb[3]) / 2) * Math.PI) / 180) || 1;
  const ratio = Math.max(1e-6, bb[3] - bb[1]) / Math.max(1e-6, (bb[2] - bb[0]) * k);
  const W = width, H = Math.round(Math.min(2400, Math.max(200, W * ratio)));
  const project = fitProjection(bb, W, H, 16);
  const land = base
    ? `<path d="${shapes(base, project).polys}" fill="#f6f4e4" fill-rule="evenodd" stroke="#8b98a8" stroke-width="1.5" stroke-linejoin="round"/>`
    : "";
  const lines = base && borders ? shapes(borders, project).lines : "";
  const top = {}; // rarest shade of each tag: the lightest tone
  for (const f of fc.features) top[f.properties.tag] = Math.max(top[f.properties.tag] ?? 0, f.properties.shade ?? 0);
  const paths = fc.features.map((f) => {
    const d = shapes(f.geometry, project).polys;
    const s = shadeFactor(f.properties.shade ?? 0, top[f.properties.tag]);
    return `<path d="${d}" fill="${f.properties.color}" fill-opacity="${(0.85 * s).toFixed(2)}" fill-rule="evenodd" stroke="#1a1a1a" stroke-opacity="${(0.6 * s).toFixed(2)}" stroke-width="1.5" stroke-linejoin="round"><title>${esc(f.properties.tag)}</title></path>`;
  });
  const bg = background ? `<rect width="${W}" height="${H}" fill="${background}"/>` : "";
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${W} ${H}" width="${W}" height="${H}">${bg}${land}${paths.join("")}${lines ? `<path d="${lines}" fill="none" stroke="#3a4350" stroke-opacity="0.75" stroke-width="1" stroke-linejoin="round"/>` : ""}</svg>`;
  return { svg, w: W, h: H };
}

export async function svgToPng({ svg, w, h }) {
  const url = URL.createObjectURL(new Blob([svg], { type: "image/svg+xml" }));
  try {
    const img = await new Promise((res, rej) => {
      const i = new Image();
      i.onload = () => res(i);
      i.onerror = () => rej(new Error("Could not render the image"));
      i.src = url;
    });
    const c = document.createElement("canvas");
    c.width = w; c.height = h;
    c.getContext("2d").drawImage(img, 0, 0, w, h);
    return await new Promise((res, rej) => c.toBlob((b) => (b ? res(b) : rej(new Error("Could not export the image"))), "image/png"));
  } finally { URL.revokeObjectURL(url); }
}
