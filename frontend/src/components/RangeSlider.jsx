// Two-handle range. Two native range inputs sit on top of each other; only their thumbs take clicks.
export default function RangeSlider({ min, max, step, value: [lo, hi], onChange, format = String, label }) {
  const pct = (v) => ((v - min) / (max - min)) * 100;
  return <div className="range-wrap">
    <div className="range">
      <div className="range-track"><i style={{ left: `${pct(lo)}%`, right: `${100 - pct(hi)}%` }} /></div>
      <input type="range" aria-label={`${label}, minimum`} min={min} max={max} step={step} value={lo}
        onChange={(e) => onChange([Math.min(Number(e.target.value), hi - step), hi])} />
      <input type="range" aria-label={`${label}, maximum`} min={min} max={max} step={step} value={hi}
        onChange={(e) => onChange([lo, Math.max(Number(e.target.value), lo + step)])} />
    </div>
    <output className="range-value">{format(lo, false)} – {format(hi, true)}</output>
  </div>;
}
