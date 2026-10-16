// Relations between clues. Keep the types in sync with RelationIn in src/api/routers/clues.py.
export const REL_TYPES = [
  ["same_as", "Same as"],
  ["similar_to", "Similar to"],
  ["group", "Group"],
];
export const REL_LABEL = Object.fromEntries(REL_TYPES);
export const NOTE_TYPES = ["same_as", "similar_to"]; // the ones that carry a sentence

export const VERBS = ["has", "uses"];
const PLURAL = { has: "have", uses: "use" };
/** "uses" -> "use" when more than one country shares the clue. */
export const verbFor = (verb, n) => (n === 1 ? verb : PLURAL[verb] ?? verb);

/** ["A"] -> "A", ["A","B"] -> "A and B", ["A","B","C"] -> "A, B and C" */
export const joinNames = (names) =>
  names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;

export const uniq = (a) => [...new Set(a)];
export const relsOf = (clue, type) => (clue.relations ?? []).filter((r) => r.type === type);
export const sameRel = (a, b) => a.type === b.type && a.cc === b.cc && a.id === b.id;

/** Splits a list of clues into blocks: clues joined by a "group" relation end up in the same block.
 *  Blocks keep the order of their first clue. */
export function groupBlocks(list) {
  const idx = new Map(list.map((c, i) => [c.id, i]));
  const parent = list.map((_, i) => i);
  const find = (i) => (parent[i] === i ? i : (parent[i] = find(parent[i])));
  list.forEach((c, i) => (c.relations ?? []).forEach((r) => {
    if (r.type === "group" && idx.has(r.id)) parent[find(i)] = find(idx.get(r.id));
  }));
  const out = new Map();
  list.forEach((c, i) => {
    const k = find(i);
    if (!out.has(k)) out.set(k, []);
    out.get(k).push(c);
  });
  return [...out.values()];
}
