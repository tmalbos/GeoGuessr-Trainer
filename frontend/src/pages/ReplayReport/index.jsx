import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { num, time, tone } from "../../lib/format.js";
import GradeIcon from "./GradeIcon.jsx";
import RoundMap from "./RoundMap.jsx";
import StageBars from "./StageBars.jsx";
import Timeline from "./Timeline.jsx";
import { GRADES, clock } from "./stages.js";
import "./replay-report.css";

const Grade = ({ id, count }) => <span className="rr-grade" style={{ "--h": GRADES[id].hue }}>
  <GradeIcon id={id} size={16} /> {GRADES[id].label}{count > 1 && ` ×${count}`}</span>;

// "12.4s" under a minute, "1m 05s" above.
const saved = (ms) => {
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, "0")}s`;
};
const SAVE_HINT = "Time you lose by moving or zooming slower than an efficient player would";
const SAVE_STYLE = { color: "var(--sand)", fontWeight: 600 };

function Summary({ s }) {
  if (!s.rounds_with_replay) return null;
  return <section className="card rr-sum">
    <StageBars steps={s.steps} timeMs={s.time_ms} size="lg" />
    {s.grades.length > 0 && <div className="rr-grades">{s.grades.map((g) => <Grade key={g.grade} id={g.grade} count={g.count} />)}</div>}
    {s.time_saved_ms > 0 && <p style={{ margin: 0 }} title={SAVE_HINT}>
      <span style={SAVE_STYLE}>⏱ {saved(s.time_saved_ms)}</span> <span className="muted">could have been saved with efficient movement and zooming</span></p>}
    {s.rounds_with_replay < s.rounds_total && <p className="muted" style={{ margin: 0 }}>
      Steps and time cover {s.rounds_with_replay} of {s.rounds_total} rounds (the rest have no replay).</p>}
  </section>;
}

function Round({ r }) {
  const [open, setOpen] = useState(false);
  const st = r.has_replay ? r.stages : null;
  return <section className="card rr">
    <div className="rr-head">
      <b>Round {r.round_number}</b>
      <span className="rr-score" style={{ color: tone(r.score) }}>{num(r.score)} pts</span>
      <span className="muted">{num(r.distance_km)} km · {time(r.time_sec)} · {r.steps} steps</span>
      {st && r.time_saved_ms > 0 && <span style={SAVE_STYLE} title={SAVE_HINT}>⏱ {saved(r.time_saved_ms)} could have been saved</span>}
    </div>
    <div className="rr-body">
      <div className="rr-main">
        <RoundMap real={r.real} guess={r.guess} path={r.path} searched={r.searched} />
        {st ? <>
          <Timeline segments={st.segments} events={r.timeline} duration={st.duration_ms} />
          <button type="button" className="btn ghost rr-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide details" : "More details"}</button>
          {open && <ol className="rr-list">{r.timeline.map((e, i) => <li key={i} className="rr-ev" title={e.note || undefined}>
            <span className="rr-t">{clock(e.t_ms)}</span>
            <span className="rr-g"><GradeIcon id={e.grade} size={22} /></span>
            <span>{e.text}{e.count > 1 && <span className="muted"> ×{e.count}</span>}</span>
          </li>)}</ol>}
        </> : <p className="muted">No replay stored for this round.</p>}
      </div>
      {st && <div className="rr-side"><StageBars steps={st.steps} timeMs={st.time_ms} /></div>}
    </div>
  </section>;
}

export default function ReplayReport({ g, title, onBack }) {
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let off = false;
    setData(null); setErr("");
    api(`/replay-report/${encodeURIComponent(g.challenge_token)}/${encodeURIComponent(g.game_id)}`)
      .then((d) => !off && setData(d))
      .catch((e) => !off && setErr(e.message));
    return () => { off = true; };
  }, [g.challenge_token, g.game_id]);

  return <>
    <button type="button" className="btn ghost" onClick={onBack}>← History</button>
    <h2 style={{ marginTop: 16 }}>Replay report</h2>
    <p className="muted">{title}</p>
    {err && <p className="err">{err}</p>}
    {!data && !err && <p className="muted">Analyzing replays…</p>}
    {data && <Summary s={data.summary} />}
    {data?.rounds.map((r) => <Round key={r.round_number} r={r} />)}
  </>;
}
