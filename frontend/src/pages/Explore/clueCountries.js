import { api } from "../../lib/api.js";

// Which countries have clues (colors the world map). Fetched once per page session and kept in memory:
// not in localStorage, so a server restart + page reload recalculates it. ClueList keeps it in sync
// (setHasClues) whenever a country's clue list is loaded, so it never needs refetching.
let cached = null; // Set of upper-case ISO codes
let pending = null;

export const clueCountriesCached = () => cached;

export function loadClueCountries() {
  if (cached) return Promise.resolve(cached);
  pending ??= api("/clues/summary")
    .then((r) => (cached = new Set(r.countries)))
    .finally(() => { pending = null; });
  return pending;
}

/** Record whether a country has clues in the normal view. No-op until the first load. */
export function setHasClues(code, has) {
  if (!cached) return;
  const c = code.toUpperCase();
  if (cached.has(c) === has) return;
  cached = new Set(cached);
  if (has) cached.add(c); else cached.delete(c);
}

export const invalidateClueCountries = () => { cached = null; };
