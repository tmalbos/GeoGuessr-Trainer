import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { num, time, tone } from "../../lib/format.js";
import RoundMap from "./RoundMap.jsx";
import StageBars from "./StageBars.jsx";
import Timeline from "./Timeline.jsx";
import { GRADES, clock } from "./stages.js";
import "./replay-report.css";

const Grade = ({ id, count }) => <span className="rr-grade" style={{ "--h": GRADES[id].hue }}>
  <b aria-hidden="true">{GRADES[id].glyph}</b> {GRADES[id].label}{count > 1 && ` ×${count}`}</span>;

function Summary({ s }) {
  if (!s.rounds_with_replay) return null;
  return <section className="card rr-sum">
    <StageBars steps={s.steps} timeMs={s.time_ms} size="lg" />
    {s.grades.length > 0 && <div className="rr-grades">{s.grades.map((g) => <Grade key={g.grade} id={g.grade} count={g.count} />)}</div>}
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
    </div>
    <div className="rr-body">
      <div className="rr-main">
        <RoundMap real={r.real} guess={r.guess} path={r.path} searched={r.searched} />
        {st ? <>
          <Timeline segments={st.segments} events={r.timeline} duration={st.duration_ms} />
          <button type="button" className="btn ghost rr-more" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
            {open ? "Hide details" : "More details"}</button>
          {open && <ol className="rr-list">{r.timeline.map((e, i) => <li key={i} className={`rr-ev ${e.tone}`}>
            <span className="rr-t">{clock(e.t_ms)}</span>
            <span className="rr-g" style={{ "--h": GRADES[e.grade]?.hue }} title={GRADES[e.grade]?.label}>{GRADES[e.grade]?.glyph}</span>
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
