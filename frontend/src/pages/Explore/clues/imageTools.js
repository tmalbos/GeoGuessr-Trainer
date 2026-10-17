// Helpers for the clue image editor: talking to the staging endpoints and editing pixels on a canvas.

const json = async (r) => {
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};

export const stageFile = (file) =>
  fetch(`/api/clues/stage/image?filename=${encodeURIComponent(file.name || "image.png")}`, { method: "POST", body: file }).then(json);
export const stageBlob = (blob) => stageFile(new File([blob], "edited.png", { type: "image/png" }));
export const stagePath = (path) =>
  fetch("/api/clues/stage-path/image", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path }) }).then(json);
export const discardStaged = (token) => { if (token) fetch(`/api/clues/staged/${token}`, { method: "DELETE" }).catch(() => {}); };
export const stagedUrl = (token) => `/api/clues/staged/${token}/image`;

// ── canvas ──────────────────────────────────────────────────────────────────
const make = (w, h) => { const c = document.createElement("canvas"); c.width = w; c.height = h; return c; };

export const loadImage = (url) => new Promise((res, rej) => {
  const i = new Image();
  i.onload = () => res(i);
  i.onerror = () => rej(new Error("Could not load the image"));
  i.src = url;
});

export function toCanvas(img) {
  const c = make(img.naturalWidth, img.naturalHeight);
  c.getContext("2d").drawImage(img, 0, 0);
  return c;
}

/** r = { x, y, w, h } in canvas pixels. */
export function cropCanvas(c, r) {
  const x = Math.max(0, Math.round(r.x)), y = Math.max(0, Math.round(r.y));
  const w = Math.max(1, Math.min(c.width - x, Math.round(r.w)));
  const h = Math.max(1, Math.min(c.height - y, Math.round(r.h)));
  const o = make(w, h);
  o.getContext("2d").drawImage(c, x, y, w, h, 0, 0, w, h);
  return o;
}

// PNG keeps the edit lossless; the server does the one lossy WebP encode.
export const toBlob = (c) => new Promise((res, rej) =>
  c.toBlob((b) => (b ? res(b) : rej(new Error("Could not export the image"))), "image/png"));

// ── smart crop ──────────────────────────────────────────────────────────────
// Trims flat borders: letterbox bars, screenshot margins, white or black padding, transparent edges.
// Works on a downscaled copy. A line counts as border when (almost) every pixel in it matches the
// colour of the outermost line of that side, within a tolerance that absorbs JPEG noise.
// Returns { x, y, w, h } in the canvas' pixels, or null when there is nothing worth trimming.
const TOL = 40;     // summed RGBA difference still counted as "the same colour"
const SHARE = 0.985; // share of a line that must match to call it border
const MIN_GAIN = 0.01; // ignore trims smaller than 1% of the image

export function autoCropRect(c) {
  const s = Math.min(1, 480 / Math.max(c.width, c.height));
  const w = Math.max(1, Math.round(c.width * s)), h = Math.max(1, Math.round(c.height * s));
  const small = make(w, h);
  const g = small.getContext("2d", { willReadFrequently: true });
  g.drawImage(c, 0, 0, w, h);
  const d = g.getImageData(0, 0, w, h).data;

  // row = true: line i is a row, j runs over x. Otherwise line i is a column, j runs over y.
  const off = (row, i, j) => (row ? i * w + j : j * w + i) * 4;
  const mean = (row, i, lo, hi) => {
    const m = [0, 0, 0, 0];
    for (let j = lo; j < hi; j++) { const o = off(row, i, j); for (let k = 0; k < 4; k++) m[k] += d[o + k]; }
    return m.map((v) => v / Math.max(1, hi - lo));
  };
  const flat = (row, i, lo, hi, ref) => {
    let ok = 0;
    for (let j = lo; j < hi; j++) {
      const o = off(row, i, j);
      if (Math.abs(d[o] - ref[0]) + Math.abs(d[o + 1] - ref[1]) + Math.abs(d[o + 2] - ref[2]) + Math.abs(d[o + 3] - ref[3]) <= TOL) ok++;
    }
    return ok >= SHARE * (hi - lo);
  };
  const walk = (row, start, end, step, lo, hi) => {
    const ref = mean(row, start, lo, hi);
    let n = 0;
    for (let i = start; i !== end && flat(row, i, lo, hi, ref); i += step) n++;
    return n;
  };

  let top = walk(true, 0, h, 1, 0, w);
  let bottom = walk(true, h - 1, -1, -1, 0, w);
  if (top + bottom >= h - 4) return null; // the whole image is flat
  let left = walk(false, 0, w, 1, top, h - bottom);
  let right = walk(false, w - 1, -1, -1, top, h - bottom);
  if (left + right >= w - 4) return null;

  // Leave a hair of margin so the content never touches the new edge.
  top = Math.max(0, top - 1); bottom = Math.max(0, bottom - 1);
  left = Math.max(0, left - 1); right = Math.max(0, right - 1);
  if ((top + bottom) / h < MIN_GAIN && (left + right) / w < MIN_GAIN) return null;

  return { x: left / s, y: top / s, w: (w - left - right) / s, h: (h - top - bottom) / s };
}
