import { choices, repair, setFilters } from "../lib/filters.js";
import { MATCH_TYPE_LABELS, MOVE_TYPE_LABELS, tlLabel } from "../lib/format.js";

/** The one filter bar for History and Analysis. Only offers combinations that have games.
 *  exact = no "All" option (Analysis needs one exact combination). Extra controls go in children. */
export default function FilterBar({ f, options, exact = false, children }) {
  const c = choices(options, f);
  const change = (patch) => setFilters(repair(options, { ...f, ...patch }, exact));
  return <div className="filter-bar">
    <div><label htmlFor="f-match">Game type</label>
      <select id="f-match" value={f.matchType} onChange={(e) => change({ matchType: e.target.value })}>
        {!exact && <option value="">All</option>}
        {c.matchTypes.map((m) => <option key={m} value={m}>{MATCH_TYPE_LABELS[m] || m}</option>)}
      </select></div>
    <div><label htmlFor="f-move">Game mode</label>
      <select id="f-move" value={f.moveType} onChange={(e) => change({ moveType: e.target.value })}>
        {!exact && <option value="">All</option>}
        {c.moveTypes.map((m) => <option key={m} value={m}>{MOVE_TYPE_LABELS[m] || m}</option>)}
      </select></div>
    <div><label htmlFor="f-time">Time limit</label>
      <select id="f-time" value={f.timeLimit} onChange={(e) => change({ timeLimit: e.target.value })}>
        {!exact && <option value="any">All</option>}
        {c.timeLimits.map((t) => <option key={t} value={t}>{tlLabel(t === "null" ? null : Number(t))}</option>)}
      </select></div>
    {children}
  </div>;
}
