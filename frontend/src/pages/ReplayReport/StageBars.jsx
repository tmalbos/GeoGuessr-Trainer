import { STAGES, clock } from "./stages.js";

// How much of the bar a segment needs to show "emoji + number", or just the emoji.
const FULL = 0.2;
const ICON = 0.07;

function Row({ title, values, fmt, size }) {
  const total = STAGES.reduce((a, s) => a + (values[s.id] ?? 0), 0);
  if (!total) return null;
  return <div className="sb-row">
    <span className="sb-title">{title}</span>
    <div className={`sb-bar ${size}`}>
      {STAGES.filter((s) => values[s.id] > 0).map((s) => {
        const share = values[s.id] / total;
        return <span key={s.id} className="sb-seg" style={{ "--h": s.hue, flex: `${values[s.id]} 1 0` }}
          title={`${s.label}: ${fmt(values[s.id])} (${Math.round(share * 100)}%)`}>
          {share >= ICON && <span aria-hidden="true">{s.emoji}</span>}
          {share >= FULL && <small>{fmt(values[s.id])}</small>}
        </span>;
      })}
    </div>
  </div>;
}

/** Where the steps and the time went, split into the four stages. Steps first: they measure efficiency. */
export default function StageBars({ steps, timeMs, size = "md" }) {
  return <div className="sb">
    <Row title="Steps" values={steps} fmt={String} size={size} />
    <Row title="Time" values={timeMs} fmt={clock} size={size} />
    <div className="sb-legend">{STAGES.map((s) => <span key={s.id} style={{ "--h": s.hue }}>
      <span aria-hidden="true">{s.emoji}</span> {s.label}</span>)}</div>
  </div>;
}
