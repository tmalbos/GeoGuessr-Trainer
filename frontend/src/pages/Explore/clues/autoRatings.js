// Automatic ratings for a NEW clue (created or imported). Pure functions, no network.
// The editor calls autoRatings() on every change and applies the result to each rating the user hasn't touched.
//
// National
//   frequency    10
//   ease         10 - log2(n), n = concepts in the text + "similar to" links
//   reliability  10 - number of "same as" links
// Regional
//   frequency    only for a mapmaking region: average distance between the points of each cluster (km), see FREQ_STEPS; 5 for any other region
//   ease         10 - log2(n), n = concepts + "similar to" links + (polygons - 1) + (shades - 1)
//   reliability  10 - (separate pieces after merging all polygons - 1); 10 when there are no stats (SVG, CSV)
// `stats` comes from the staging endpoint (src/geo/region_stats.py): { polygons, shades, components, spread_km }.

// Name AreaPicker gives the file it stages for a mapmaking region.
export const MAPMAKING_FILE = "mapmaking.geojson";

// [max average distance in km, frequency]. Above the last step the frequency would be 0; the dots start at 1.
const FREQ_STEPS = [[2, 10], [4, 9], [6, 8], [10, 7], [16, 6], [26, 5], [42, 4], [68, 3], [110, 2], [178, 1]];
export const frequencyFromKm = (km) => FREQ_STEPS.find(([max]) => km <= max)?.[1] ?? 1;

// ── concepts: no LLM, just a cheap guess. Each clause of the text that says something is one concept. ──
// The text is cut at sentence ends, commas, semicolons, brackets, slashes and linking words (and, or, but, with...);
// a piece counts when it still has a word that isn't filler. Repeated pieces count once.
const STOP = new Set(("a an the is are was were be been being am of in on at to for from by it its this that these those there here as so " +
  "very can may might will would should could also often usually mostly mainly only some all any has have had do does did not no if than " +
  "you your they their them he she his her we our i about into over under near around which who whom what when where how just more most " +
  "many much such other another both each every always never sometimes seen found see look looks looking called known like get gets " +
  "el la los las de del en y o un una que es son").split(" "));

const SPLIT = /[.!?]+(?=\s|$)|:(?=\s|$)|[;,\n()\[\]"“”•·|\/&+]|\s[-–—]+\s|\s(?:and|or|but|also|plus|while|whereas|then|with|without|except|unless|instead)\s/giu;

const plain = (t) => t
  .replace(/\bNOTES?\s*:[^\n]*/gi, " ")
  .replace(/\[([^\]]+)\]\(https?:\/\/[^\s)]+\)/g, "$1")
  .replace(/https?:\/\/\S+/g, " ")
  .replace(/[*_]{1,2}/g, "");

export function countConcepts(text) {
  const seen = new Set();
  for (const piece of plain(text ?? "").split(SPLIT)) {
    const words = (piece.toLowerCase().match(/[\p{L}\p{N}][\p{L}\p{N}'’-]*/gu) ?? [])
      .filter((w) => !STOP.has(w) && (w.length > 1 || /\d/.test(w)));
    if (words.length) seen.add(words.join(" "));
  }
  return seen.size;
}

const rate = (n) => String(Math.max(1, Math.min(10, Math.round(n))));

/** -> { frequency, ease, reliability }: each a "1".."10" string, or "" when it can't be worked out (leave empty). */
export function autoRatings({ scope, info, relations, stats, fromMapmaking }) {
  const out = { frequency: "", ease: "", reliability: "" };
  const rels = relations ?? [];
  const sameAs = rels.filter((r) => r.type === "same_as").length;
  const similar = rels.filter((r) => r.type === "similar_to").length;
  const regional = scope === "reg";

  const concepts = countConcepts(info);
  if (concepts) {
    const region = regional && stats ? Math.max(0, stats.polygons - 1) + Math.max(0, stats.shades - 1) : 0;
    out.ease = rate(10 - Math.log2(concepts + similar + region));
  }

  if (!regional) out.reliability = rate(10 - sameAs);
  else out.reliability = rate(10 - Math.max(0, (stats?.components ?? 1) - 1));

  if (!regional) out.frequency = "10";
  else if (fromMapmaking && stats && stats.spread_km != null) out.frequency = rate(frequencyFromKm(stats.spread_km));
  else out.frequency = "5";
  return out;
}
