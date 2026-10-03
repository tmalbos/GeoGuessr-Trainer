import Flag from "../../components/Flag.jsx";
import { num, tone } from "../../lib/format.js";
import { kmToScore } from "../../lib/tiers.js";
import { ConfusionRow, TierChip, TrendBadge } from "./parts.jsx";
import TierLadder from "./TierLadder.jsx";

const BookIcon = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <path d="M4 5a2 2 0 0 1 2-2h13v16H6a2 2 0 0 0-2 2z" /><path d="M4 21V5" /></svg>;

// Plotted as score, so up = better (the raw numbers are distances, where down is better).
function Spark({ data }) {
  if (data.length < 2) return null;
  const pts = data.map(kmToScore);
  const lo = Math.min(...pts), span = Math.max(Math.max(...pts) - lo, 1), w = 240, h = 40, pad = 4;
  const line = pts.map((v, i) => `${(i / (pts.length - 1)) * w},${pad + (1 - (v - lo) / span) * (h - 2 * pad)}`).join(" ");
  return <figure className="spark">
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="40" preserveAspectRatio="none" role="img" aria-label="Your score per round, oldest to newest">
      <polyline points={line} fill="none" stroke="var(--sea)" strokeWidth="1.5" vectorEffect="non-scaling-stroke" /></svg>
    <figcaption className="muted">last {data.length} rounds</figcaption>
  </figure>;
}

export default function Zone({ z, tiers, open, onToggle }) {
  const title = z.name === "_global_" ? "Global" : z.name;
  return <div className={`card zone ${open ? "open" : ""}`}>
    <button type="button" className="zone-head" aria-expanded={open} onClick={onToggle}>
      <span className="chev" aria-hidden="true">▸</span>
      {z.code && <Flag code={z.code} width={24} />}
      <b className="zone-name">{title}</b>
      <span className="muted zone-n">{z.total} rounds</span>
      <span className="zone-sum">
        <TierChip tiers={tiers} label={z.level_label} />
        <TierLadder tiers={tiers} score={z.score_high} compact />
        <TrendBadge arrow={z.level_arrow} />
      </span>
    </button>
    {open && <div className="zone-body">
      <div className="stats">
        <div><span className="muted">Current level</span>
          <div className="zone-stat" style={{ color: tone(z.score_high ?? 0) }}><TierChip tiers={tiers} label={z.level_label} /><TrendBadge arrow={z.level_arrow} /></div>
          <TierLadder tiers={tiers} score={z.score_high} ciLo={z.ci_lo} ciHi={z.ci_hi} /></div>
        <div><span className="muted">Worst rounds</span>
          <div className="zone-stat"><TierChip tiers={tiers} label={z.p90_label} /><TrendBadge arrow={z.p90_arrow} /></div>
          <span className="muted">worst 10% ≈ {num(z.p90_now_km)} km</span></div>
        <div><span className="muted">Consistency</span>
          <div className="zone-stat"><TierChip tiers={tiers} label={z.cons_label} /><TrendBadge arrow={z.cons_arrow} /></div>
          <span className="muted">typical ±{num(z.std_now_km)} km</span></div>
      </div>
      <Spark data={z.series} />
      {z.confusions?.[0] && <div className="conf-block"><span className="muted conf-title">Top mix-up</span><ConfusionRow c={z.confusions[0]} /></div>}
      {z.child_confusions?.length > 0 && <div className="conf-block">
        <span className="muted conf-title">Mix-ups by {z.child_level}</span>
        {z.child_confusions.map((c, i) => <ConfusionRow key={i} c={c} />)}</div>}
      {z.code && <a className="zone-link" href={`#/explore/${z.code}`}><BookIcon />Clues for {title}</a>}
    </div>}
  </div>;
}
