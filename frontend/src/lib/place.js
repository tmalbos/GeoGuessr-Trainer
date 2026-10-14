import { ApiError } from "./api.js";

// Real-map windows from the backend (/api/minigames). The payload is binary; see src/minigames/window.py.
// Local coordinates: metres, origin at the window centre, +x east, +y north.

const TYPED = { f32: Float32Array, u32: Uint32Array, u8: Uint8Array };

/** Parts of a layer grouped by a key (Uint32Array of part indices per key), in one pass. */
function group(count, keyOf, nKeys) {
  const sizes = new Uint32Array(nKeys);
  for (let i = 0; i < count; i++) sizes[keyOf(i)]++;
  const out = Array.from(sizes, (n) => new Uint32Array(n));
  const fill = new Uint32Array(nKeys);
  for (let i = 0; i < count; i++) { const k = keyOf(i); out[k][fill[k]++] = i; }
  return out;
}

export async function fetchPlace(sizeKm, country, signal) {
  const t0 = performance.now();
  let r;
  try {
    r = await fetch(`/api/minigames/place?size_km=${sizeKm}${country ? `&country=${encodeURIComponent(country)}` : ""}`, { signal });
  } catch (e) {
    if (e.name === "AbortError") throw e;
    throw new ApiError("Can't reach the app server. Make sure it's running, then retry.", { detail: "Network error" });
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    const detail = typeof body.detail === "string" ? body.detail : "";
    throw new ApiError(detail || "Couldn't get a place. Try again.", { status: r.status, detail });
  }
  const buf = await r.arrayBuffer();
  const t1 = performance.now();

  const headLen = new DataView(buf).getUint32(0, true);
  const meta = JSON.parse(new TextDecoder().decode(new Uint8Array(buf, 4, headLen)));
  const base = 4 + headLen;
  const layers = {};
  for (const [lname, c] of Object.entries(meta.layers)) layers[lname] = { count: c.count, pts: c.pts };
  for (const [key, a] of Object.entries(meta.arrays)) {
    const [lname, aname] = key.split(".");
    layers[lname][aname] = new TYPED[a.t](buf, base + a.off, a.n);
  }

  // Roads: normal / bridge / tunnel, each split by class, so drawing is one path per group.
  const rd = layers.roads;
  rd.groups = [0, 1, 2].map(() => null);
  const mode = (i) => (rd.flag[i] & 2 ? 2 : rd.flag[i] & 1 ? 1 : 0); // tunnel, bridge, normal
  const keyed = group(rd.count, (i) => mode(i) * 8 + rd.cls[i], 24);
  rd.groups = [0, 1, 2].map((m) => keyed.slice(m * 8, m * 8 + 8));
  layers.water_lines.groups = group(layers.water_lines.count, (i) => Math.min(2, layers.water_lines.kind[i]), 3);
  const t2 = performance.now();

  return { meta, layers, timing: { fetch: Math.round(t1 - t0), decode: Math.round(t2 - t1), bytes: buf.byteLength } };
}
