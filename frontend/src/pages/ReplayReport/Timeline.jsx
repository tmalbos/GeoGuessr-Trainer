import GradeIcon from "./GradeIcon.jsx";
import { GRADES, clock, stageOf } from "./stages.js";

/** One bar for the whole round: colored by stage, with a dot for every event.
 *  Graded events are colored by their grade (the notable ones carry the chess icon).
 *  Clicking a dot selects that event (shows it on the map); clicking it again deselects.
 *  It draws whatever events it is given, so trimming the report needs no change here. */
export default function Timeline({ segments, events, duration, selected, onSelect }) {
  if (!duration) return null;
  const pct = (t) => Math.min(100, Math.max(0, (t / duration) * 100));
  return <div className="tl">
    <div className="tl-track" role="group" aria-label="Timeline of the round">
      <div className="tl-bands">{segments.map((s, i) =>
        <i key={i} className="tl-seg" style={{ "--h": stageOf(s.stage)?.hue ?? 215, left: `${pct(s.t0)}%`, width: `${pct(s.t1) - pct(s.t0)}%` }} />)}</div>
      {events.map((e, i) => {
        const g = GRADES[e.grade];
        const can = !!e.focus;
        return <button type="button" key={i}
          className={`tl-ev ${g ? (g.big ? "gb" : "gs") : ""} ${can ? "can" : ""} ${selected === i ? "sel" : ""}`}
          style={{ left: `${pct(e.t_ms)}%`, "--h": g?.hue }}
          aria-pressed={can ? selected === i : undefined}
          aria-label={`${clock(e.t_ms)} ${e.text}`}
          onClick={() => can && onSelect(i)}
          title={`${clock(e.t_ms)} · ${g ? `${g.label}: ` : ""}${e.text}${e.count > 1 ? ` ×${e.count}` : ""}${e.note ? `\n${e.note}` : ""}`}>
          {g?.big && <GradeIcon id={e.grade} size={20} />}
        </button>;
      })}
    </div>
    <div className="tl-axis muted"><span>0:00</span><span>{clock(duration)}</span></div>
  </div>;
}
