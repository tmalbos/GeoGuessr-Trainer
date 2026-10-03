import { kmToScore, scorePos, tierHue } from "../../lib/tiers.js";

/** Tier ladder with a "you are here" dot. Full size also draws the likely range (95% interval, km). */
export default function TierLadder({ tiers, score, ciLo, ciHi, compact }) {
  if (score == null || !tiers?.length) return null;
  const n = tiers.length;
  const dot = scorePos(tiers, score);
  const current = Math.min(n - 1, Math.floor(dot * n));
  const band = !compact && ciLo != null && ciHi != null ? [scorePos(tiers, kmToScore(ciHi)), scorePos(tiers, kmToScore(ciLo))] : null;
  const hint = band ? `Typical distance between ${Math.round(ciLo)} and ${Math.round(ciHi)} km` : undefined;
  return <div className={`ladder ${compact ? "compact" : ""}`} role="img" aria-label={`Score ${score} out of 5000, ${tiers[current].label} tier${hint ? `. ${hint}` : ""}`}>
    <div className="ladder-segs">{tiers.map((t, i) =>
      <i key={t.label} title={t.label} className={i === current ? "on" : ""} style={{ "--h": tierHue(i, n) }} />)}</div>
    {band && <i className="ladder-ci" title={hint} style={{ left: `${band[0] * 100}%`, width: `${Math.max(1, (band[1] - band[0]) * 100)}%` }} />}
    <i className="ladder-dot" style={{ left: `${dot * 100}%` }} />
  </div>;
}
