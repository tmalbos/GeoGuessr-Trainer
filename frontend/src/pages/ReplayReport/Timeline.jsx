import { GRADES, clock, stageOf } from "./stages.js";

/** One bar for the whole round: colored by stage, with a dot for every event.
 *  Graded events are colored by their grade (the notable ones carry the chess symbol).
 *  It draws whatever events it is given, so trimming the report needs no change here. */
export default function Timeline({ segments, events, duration }) {
  if (!duration) return null;
  const pct = (t) => Math.min(100, Math.max(0, (t / duration) * 100));
  return <div className="tl">
    <div className="tl-track" role="img" aria-label="Timeline of the round">
      <div className="tl-bands">{segments.map((s, i) =>
        <i key={i} className="tl-seg" style={{ "--h": stageOf(s.stage)?.hue ?? 215, left: `${pct(s.t0)}%`, width: `${pct(s.t1) - pct(s.t0)}%` }} />)}</div>
      {events.map((e, i) => {
        const g = GRADES[e.grade];
        return <span key={i} className={`tl-ev ${g ? (g.big ? "gb" : "gs") : ""}`}
          style={{ left: `${pct(e.t_ms)}%`, "--h": g?.hue }}
          title={`${clock(e.t_ms)} · ${g ? `${g.label}: ` : ""}${e.text}${e.count > 1 ? ` ×${e.count}` : ""}`}>
          {g?.big && g.glyph}
        </span>;
      })}
    </div>
    <div className="tl-axis muted"><span>0:00</span><span>{clock(duration)}</span></div>
  </div>;
}
