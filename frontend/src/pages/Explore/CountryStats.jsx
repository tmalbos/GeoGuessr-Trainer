import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { setFilters, useFilterOptions } from "../../lib/filters.js";
import { TierChip, TrendBadge } from "../Analysis/parts.jsx";
import TierLadder from "../Analysis/TierLadder.jsx";

const MIN_ROUNDS = 10; // keep in sync with MIN_ROUNDS in src/api/routers/analysis.py

/** Your level in this country, always for your main game mode (daily or duel, whichever you play most),
 *  never the active filters. Links to the full analysis, switching its filters to that mode. */
export default function CountryStats({ code, name }) {
  const { options } = useFilterOptions();
  const [d, setD] = useState(null);

  useEffect(() => {
    let off = false;
    const ac = new AbortController();
    setD(null);
    api(`/analysis/country/${code.toUpperCase()}`, { signal: ac.signal })
      .then((r) => !off && setD(r)).catch(() => !off && setD({ total: 0, zone: null }));
    return () => { off = true; ac.abort(); };
  }, [code]);

  if (!d || !d.total || !options) return null;
  const z = d.zone;
  return <a className="card cs" href={`#/analysis/country?q=${encodeURIComponent(name)}`} title="Open in Analysis"
    onClick={() => d.mode && setFilters({ matchType: d.mode.match_type, moveType: d.mode.move_type, timeLimit: d.mode.time_limit })}>
    {z ? <>
      <div className="cs-row"><TierChip tiers={options.tiers} label={z.level_label} /><TrendBadge arrow={z.level_arrow} />
        <span className="muted">{d.total} rounds</span></div>
      <TierLadder tiers={options.tiers} score={z.score_high} compact />
    </> : <>
      <div className="cs-row"><span className="muted">{d.total} / {MIN_ROUNDS} rounds</span></div>
      <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={MIN_ROUNDS} aria-valuenow={d.total}><i style={{ width: `${(d.total / MIN_ROUNDS) * 100}%` }} /></div>
    </>}
  </a>;
}
