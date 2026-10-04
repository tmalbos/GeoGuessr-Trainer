import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { num, time, tone } from "../../lib/format.js";
import RoundMap from "./RoundMap.jsx";
import "./replay-report.css";

function Round({ r }) {
  return <section className="card rr">
    <div className="rr-head">
      <b>Round {r.round_number}</b>
      <span className="rr-score" style={{ color: tone(r.score) }}>{num(r.score)} pts</span>
      <span className="muted">{num(r.distance_km)} km · {time(r.time_sec)} · {r.steps} steps</span>
    </div>
    <div className="rr-body">
      {r.has_replay
        ? <ol className="rr-list">{r.timeline.map((e, i) => <li key={i} className={`rr-ev ${e.tone}`}>
            <span className="rr-t">{time(Math.round(e.t_ms / 1000))}</span>
            <span>{e.text}{e.count > 1 && <span className="muted"> ×{e.count}</span>}</span>
          </li>)}</ol>
        : <p className="muted">No replay stored for this round.</p>}
      <div className="rr-side">
        <RoundMap real={r.real} guess={r.guess} path={r.path} searched={r.searched} />
      </div>
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
    {data?.rounds.map((r) => <Round key={r.round_number} r={r} />)}
  </>;
}
