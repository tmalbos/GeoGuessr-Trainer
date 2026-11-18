// Colouring of map-only clues by the START of each area's name (phone area codes: 8X, 9X...).
// A name may list overlay areas with "/" (41/42): each part is read on its own.
// Depth = how many characters of the names are looked at. A depth only counts when it tells apart more areas than
// the one before, so a filler start shared by every name (0xx) never gives a depth of its own.

const parts = (name) => String(name ?? "").split("/").map((a) => a.replace(/[^\p{L}\p{N}]/gu, "").toUpperCase()).filter(Boolean);
const prefixes = (name, d) => [...new Set(parts(name).map((p) => p.slice(0, d)))];

/** The depths worth offering, lowest first (empty when the names can't be told apart by their start). */
export function depthOptions(names) {
  const longest = Math.max(0, ...names.flatMap((n) => parts(n).map((p) => p.length)));
  const out = [];
  let seen = 1;
  for (let d = 1; d <= longest; d++) {
    const count = new Set(names.flatMap((n) => prefixes(n, d))).size;
    if (count > seen) { out.push(d); seen = count; }
  }
  return out;
}

const merge = (geoms) => ({
  type: "MultiPolygon",
  coordinates: geoms.flatMap((g) => (g.type === "Polygon" ? [g.coordinates] : g.type === "MultiPolygon" ? g.coordinates : [])),
});

/** A drawn layer (names, colors, geometries...) regrouped at `depth`: one area per prefix, one hue per lowest-depth
 *  prefix and lighter / darker shades of it for the prefixes inside. */
export function groupLayer(g, depth, options) {
  const top = [...new Set(g.names.map((n) => prefixes(n, options[0])[0] ?? ""))].sort();
  const groups = new Map();
  g.names.forEach((n, i) => {
    const key = prefixes(n, depth)[0] ?? "";
    if (!groups.has(key)) groups.set(key, { top: prefixes(n, options[0])[0] ?? "", idx: [] });
    groups.get(key).idx.push(i);
  });
  const siblings = {};
  for (const [key, v] of groups) (siblings[v.top] ??= []).push(key);
  const out = { ...g, geometries: [], names: [], colors: [], extras: [], extra_blurs: [], extra_heights: [] };
  for (const [key, v] of [...groups].sort((a, b) => a[0].localeCompare(b[0]))) {
    const sib = siblings[v.top].sort();
    const hue = Math.round((top.indexOf(v.top) * 137.5) % 360);
    const light = sib.length < 2 ? 55 : Math.round(38 + (34 * sib.indexOf(key)) / (sib.length - 1));
    const one = v.idx.length === 1 ? v.idx[0] : null;
    out.geometries.push(one != null ? g.geometries[one] : merge(v.idx.map((i) => g.geometries[i])));
    out.names.push(key);
    out.colors.push(`hsl(${hue} 65% ${light}%)`);
    out.extras.push(one != null ? g.extras?.[one] ?? "" : "");
    out.extra_blurs.push(one != null ? !!g.extra_blurs?.[one] : false);
    out.extra_heights.push(one != null ? g.extra_heights?.[one] ?? 0 : 0);
  }
  return out;
}
