import { api } from "../../../lib/api.js";

// The draw editors send the whole list of actions on every build / fill. A shape's geometry can be big, so each shape
// carries a `gid` and its geometry travels only until the server has confirmed it holds that gid (see _shape_geom in
// src/api/routers/clue_draw.py). If the server forgot one (restart, eviction) it answers 410 and the request is
// repeated with every geometry.

export const newGid = () => (globalThis.crypto?.randomUUID
  ? globalThis.crypto.randomUUID().replaceAll("-", "")
  : `${Date.now().toString(36)}${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2)}`);

/** The item as the server wants it: no drawing-only fields, and no geometry for a shape the server already holds. */
export function wire(item, known) {
  const { d: _d, w: _w, ...rest } = item;
  if (rest.gid && known.has(rest.gid)) delete rest.geometry;
  return rest;
}

export const markKnown = (items, known) => { for (const i of items) if (i.gid) known.add(i.gid); };

/** POST `body()` to `path`; if the server lost a shape (410), forget what it knew and send everything once more. */
export async function sendItems(known, path, body, opts = {}) {
  const run = () => api(path, { method: "POST", body: body(), ...opts });
  try {
    return await run();
  } catch (e) {
    if (e?.status !== 410) throw e;
    known.clear();
    return run();
  }
}

/** Features whose geometry did not change since the last call keep the same geometry object (`seen`: name -> { s, g }),
 *  so everything cached per geometry object (the name spots) survives a rebuild. */
export function reuseGeometry(seen, features) {
  return features.map((f) => {
    const s = JSON.stringify(f.geometry);
    const old = seen.get(f.properties.name);
    if (old && old.s === s) return { ...f, geometry: old.g };
    seen.set(f.properties.name, { s, g: f.geometry });
    return f;
  });
}
