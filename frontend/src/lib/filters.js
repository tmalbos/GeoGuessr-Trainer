import { useEffect, useMemo, useState, useSyncExternalStore } from "react";
import { api, errorText } from "./api.js";
import { tlKey } from "./format.js";

// Filters shared by History, Analysis and the Explore stats. Remembered in localStorage.
export const MAX_TOTAL = 25000; // 5 rounds x 5000
const KEY = "ggt.filters.v1";
export const DEFAULTS = { matchType: "", moveType: "", timeLimit: "any", minScore: 0, maxScore: MAX_TOTAL };
const FIELDS = ["matchType", "moveType", "timeLimit"];

const read = () => {
  try { return { ...DEFAULTS, ...JSON.parse(localStorage.getItem(KEY) || "{}") }; } catch { return { ...DEFAULTS }; }
};
let state = read();
const subs = new Set();
const subscribe = (cb) => { subs.add(cb); return () => subs.delete(cb); };

export function setFilters(patch) {
  state = { ...state, ...patch };
  try { localStorage.setItem(KEY, JSON.stringify(state)); } catch { /* storage unavailable */ }
  subs.forEach((cb) => cb());
}
export const resetFilters = () => setFilters({ ...DEFAULTS });
export const useFilters = () => useSyncExternalStore(subscribe, () => state);

// ── Which combinations actually have games (fetched once, refreshed after a sync) ──
let options = null;
let pending = null;
const optSubs = new Set();
const optSubscribe = (cb) => { optSubs.add(cb); return () => optSubs.delete(cb); };

export function loadOptions(force = false) {
  if (!pending || force) {
    pending = api("/analysis/filters")
      .then((o) => { options = o; optSubs.forEach((cb) => cb()); return o; })
      .catch((e) => { pending = null; throw e; });
  }
  return pending;
}

export function useFilterOptions() {
  const o = useSyncExternalStore(optSubscribe, () => options);
  const [error, setError] = useState("");
  useEffect(() => { loadOptions().catch((e) => setError(errorText(e))); }, []);
  return { options: o, error, reload: () => { setError(""); loadOptions(true).catch((e) => setError(errorText(e))); } };
}

const uniq = (a) => [...new Set(a)];
const byLimit = (a, b) => (a === "null") - (b === "null") || Number(a) - Number(b);

/** Options that still lead to at least one game, given the other selections. */
export function choices(opts, f) {
  const c = opts.combos;
  const m = (x) => !f.matchType || x.match_type === f.matchType;
  const v = (x) => !f.moveType || x.move_type === f.moveType;
  return {
    matchTypes: uniq(c.map((x) => x.match_type)),
    moveTypes: uniq(c.filter(m).map((x) => x.move_type)),
    timeLimits: uniq(c.filter((x) => m(x) && v(x)).map((x) => tlKey(x.time_limit_sec))).sort(byLimit),
  };
}

/** Fix a selection that points at nothing. `exact` = no "All" allowed (Analysis needs one combo). */
export function repair(opts, f, exact) {
  const next = { ...f };
  const def = opts.default;
  let ch = choices(opts, next);
  if (next.matchType && !ch.matchTypes.includes(next.matchType)) next.matchType = "";
  if (exact && !next.matchType) next.matchType = (ch.matchTypes.includes(def.match_type) ? def.match_type : ch.matchTypes[0]) ?? "";
  ch = choices(opts, next);
  if (next.moveType && !ch.moveTypes.includes(next.moveType)) next.moveType = "";
  if (exact && !next.moveType) next.moveType = (ch.moveTypes.includes(def.move_type) ? def.move_type : ch.moveTypes[0]) ?? "";
  ch = choices(opts, next);
  if (next.timeLimit !== "any" && !ch.timeLimits.includes(next.timeLimit)) next.timeLimit = "any";
  if (exact && next.timeLimit === "any") next.timeLimit = (ch.timeLimits.includes(tlKey(def.time_limit_sec)) ? tlKey(def.time_limit_sec) : ch.timeLimits[0]) ?? "any";
  return next;
}

/** Shared filters, repaired against the real data. With persist, a repaired value is written back. */
export function useAppFilters(exact = false, { persist = true } = {}) {
  const stored = useFilters();
  const { options: opts, error, reload } = useFilterOptions();
  const f = useMemo(() => (opts ? repair(opts, stored, exact) : stored), [opts, stored, exact]);
  useEffect(() => {
    if (persist && opts && FIELDS.some((k) => f[k] !== stored[k])) setFilters(Object.fromEntries(FIELDS.map((k) => [k, f[k]])));
  }, [persist, opts, f, stored]);
  return { f, options: opts, error, reload, ready: !!opts };
}
