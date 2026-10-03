import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { useAppFilters } from "../../lib/filters.js";
import { TierChip, TrendBadge } from "../Analysis/parts.jsx";
import TierLadder from "../Analysis/TierLadder.jsx";

const MIN_ROUNDS = 10; // keep in sync with MIN_ROUNDS in src/api/routers/analysis.py

/** Your level in this country, from the same filters as Analysis. Links to the full analysis. */
export default function CountryStats({ code, name }) {
  const { f, options, ready } = useAppFilters(true, { persist: false });
  const [d, setD] = useState(null);
  const usable = ready && !!f.matchType && !!f.moveType && f.timeLimit !== "any";

  useEffect(() => {
    if (!usable) return undefined;
    let off = false;
    setD(null);
    api(`/analysis/country/${code.toUpperCase()}?match_type=${f.matchType}&move_type=${f.moveType}&time_limit=${f.timeLimit}`)
      .then((r) => !off && setD(r)).catch(() => !off && setD({ total: 0, zone: null }));
    return () => { off = true; };
  }, [usable, code, f.matchType, f.moveType, f.timeLimit]);

  if (!d || !d.total) return null;
  const z = d.zone;
  return <a className="card cs" href={`#/analysis/country?q=${encodeURIComponent(name)}`} title="Open in Analysis">
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
