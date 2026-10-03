import Flag from "../../components/Flag.jsx";
import { num } from "../../lib/format.js";
import { tierHue, tierIndex } from "../../lib/tiers.js";

export function TierChip({ tiers, label }) {
  const i = tierIndex(tiers, label);
  if (i < 0) return <span className="tier none">{label || "—"}</span>;
  return <span className="tier" style={{ "--h": tierHue(i, tiers.length) }}>{label}</span>;
}

// Backend arrows already mean "better" (↑) or "worse" (↓), whatever the underlying number does.
const TREND = {
  "↑": { cls: "up", icon: "▲", text: "Improving" },
  "↓": { cls: "down", icon: "▼", text: "Slipping" },
  "→": { cls: "flat", icon: "●", text: "Steady" },
};
export function TrendBadge({ arrow, hint = "Last 10 rounds compared with the 20 before" }) {
  const t = TREND[arrow] ?? TREND["→"];
  return <span className={`trend ${t.cls}`} title={hint}><span aria-hidden="true">{t.icon}</span>{t.text}</span>;
}

const Place = ({ name, code }) => <span className="conf-place">{code && <Flag code={code} width={22} />}{name}</span>;

/** "Spain → Portugal ×5 ~300 km": the real place, what you guessed, how often, how far off. */
export function ConfusionRow({ c }) {
  return <div className="conf-row" title="Real place → what you guessed">
    <Place name={c.real} code={c.real_code} />
    <span className="conf-arrow" role="img" aria-label="guessed as">→</span>
    <Place name={c.guess} code={c.guess_code} />
    <span className="conf-n" title="Times it happened">×{c.freq}</span>
    <span className="muted">~{num(c.avg_km)} km</span>
  </div>;
}
