import { num, tone } from "../../lib/format.js";

function Spark({ data }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1), w = 240, h = 40;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - (v / max) * h}`).join(" ");
  return <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="40" role="img" aria-label="Distance per round, oldest to newest"><polyline points={pts} fill="none" stroke="var(--sea)" strokeWidth="1.5" /></svg>;
}

export default function Zone({ z }) {
  const title = z.name === "_global_" ? "Global" : z.name;
  const ci = z.ci_lo != null ? ` (${num(z.ci_lo)}–${num(z.ci_hi)} km)` : "";
  return <div className="card">
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <b style={{ fontSize: 18 }}>{title}</b><span className="muted">{z.total} rounds</span></div>
    <div className="stats">
      <div><span className="muted">Current level</span><b className="score" style={{ color: tone(z.score_high ?? 0) }}>{z.level_label} {z.level_arrow}</b><span className="muted">{ci}</span></div>
      <div><span className="muted">Worst rounds</span><b>{z.p90_label} {z.p90_arrow}</b><span className="muted">worst 10% ≈ {num(z.p90_now_km)} km</span></div>
      <div><span className="muted">Consistency</span><b>{z.cons_label} {z.cons_arrow}</b><span className="muted">typical ±{num(z.std_now_km)} km</span></div>
    </div>
    <Spark data={z.series} />
    {z.confusions[0] && <div className="conf"><b>Key confusion:</b> {z.confusions[0].real} → {z.confusions[0].guess} ({z.confusions[0].freq}×, ~{num(z.confusions[0].avg_km)} km)</div>}
    {z.child_confusions.length > 0 && <div className="conf"><b>Confusions ({z.child_level}):</b>
      {z.child_confusions.map((c, i) => <div key={i}>• {c.real} → {c.guess} ({c.freq}×, ~{num(c.avg_km)} km)</div>)}</div>}
  </div>;
}
