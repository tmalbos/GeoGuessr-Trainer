import { useState } from "react";
import "./rating-dots.css";

/** 10 dots: click a dot to rate, click the same one again (or ×) to clear. Used in the clue editor only.
 *  While hovering, the current value stays solid and the dots a click would change (added or dropped) are lighter. */
export default function RatingDots({ label, value, onChange, lo, hi }) {
  const v = value === "" || value == null ? 0 : Number(value);
  const [hover, setHover] = useState(0);
  const key = (e) => {
    if (e.key === "ArrowRight" || e.key === "ArrowUp") { e.preventDefault(); onChange(String(Math.min(10, v + 1))); }
    if (e.key === "ArrowLeft" || e.key === "ArrowDown") { e.preventDefault(); onChange(v <= 1 ? "" : String(v - 1)); }
  };
  const state = (n) => {
    if (!hover) return n <= v ? "on" : "";
    if (hover === v) return n <= v ? "pv" : ""; // clicking the current value clears it
    if (hover > v) return n <= v ? "on" : n <= hover ? "pv" : "";
    return n <= hover ? "on" : n <= v ? "pv" : "";
  };
  return <div className="rdots-row">
    <span className="rdots-label">{label}</span>
    {lo && <small className="muted">{lo}</small>}
    <div className="rdots" role="radiogroup" aria-label={label} onMouseLeave={() => setHover(0)} onKeyDown={key}>
      {Array.from({ length: 10 }, (_, i) =>
        <button key={i} type="button" role="radio" aria-checked={v === i + 1} aria-label={`${label} ${i + 1}`} tabIndex={i + 1 === (v || 1) ? 0 : -1}
          className={state(i + 1)} onMouseEnter={() => setHover(i + 1)} onClick={() => onChange(v === i + 1 ? "" : String(i + 1))} />)}
    </div>
    {hi && <small className="muted">{hi}</small>}
    <button type="button" className="rate-clear" aria-label={`Clear ${label}`} disabled={!v} onClick={() => onChange("")}>×</button>
  </div>;
}
