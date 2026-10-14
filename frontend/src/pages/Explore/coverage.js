import { api } from "../../lib/api.js";

export const COVERAGE_LABELS = {
  none: "No coverage",
  official: "Official coverage",
  trekker: "Trekker coverage",
  unofficial: "Unofficial coverage",
  speculated: "Speculated coverage",
};

// Explicit states from data/coverage.txt ({ CC: state }). Fetched once per page load and kept in memory.
let cached = null;
let pending = null;

export const coverageCached = () => cached;

export function loadCoverage() {
  if (cached) return Promise.resolve(cached);
  pending ??= api("/coverage")
    .then((r) => (cached = r.coverage))
    .finally(() => { pending = null; });
  return pending;
}
