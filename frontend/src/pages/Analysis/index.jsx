import { useEffect, useState } from "react";
import { api } from "../../lib/api.js";
import { MATCH_TYPE_LABELS, MOVE_TYPE_LABELS, tlKey, tlLabel } from "../../lib/format.js";
import Zone from "./Zone.jsx";

export default function Analysis() {
  const [filters, setFilters] = useState(null);
  const [matchType, setMatchType] = useState(null);
  const [moveType, setMoveType] = useState(null);
  const [timeLimit, setTimeLimit] = useState();
  const [levels, setLevels] = useState(null);
  const [level, setLevel] = useState(null);
  const [data, setData] = useState(null);

  useEffect(() => {
    api("/analysis/filters").then((f) => {
      setFilters(f);
      setMatchType(f.default.match_type);
      setMoveType(f.default.move_type);
      setTimeLimit(f.default.time_limit_sec);
    });
  }, []);

  useEffect(() => {
    if (matchType == null) return;
    setLevels(null); setLevel(null); setData(null);
    api(`/analysis/levels?match_type=${matchType}&move_type=${moveType}&time_limit=${tlKey(timeLimit)}`)
      .then((l) => { setLevels(l); setLevel(l[0]?.level); })
      .catch(() => setLevels([]));
  }, [matchType, moveType, timeLimit]);

  useEffect(() => {
    if (level) {
      setData(null);
      api(`/analysis/${level}?match_type=${matchType}&move_type=${moveType}&time_limit=${tlKey(timeLimit)}`).then(setData);
    }
  }, [level]);

  if (!filters || matchType == null) return <><h2>Analysis</h2><p className="muted">Loading…</p></>;

  return <><h2>Analysis</h2>
    <div className="tabs" style={{ marginBottom: 8, gap: 10 }}>
      <select value={matchType} onChange={(e) => setMatchType(e.target.value)}>
        {filters.match_types.map((m) => <option key={m} value={m}>{MATCH_TYPE_LABELS[m] || m}</option>)}
      </select>
      <select value={moveType} onChange={(e) => setMoveType(e.target.value)}>
        {filters.move_types.map((m) => <option key={m} value={m}>{MOVE_TYPE_LABELS[m] || m}</option>)}
      </select>
      <select value={tlKey(timeLimit)} onChange={(e) => setTimeLimit(e.target.value === "null" ? null : Number(e.target.value))}>
        {filters.time_limits.map((t) => <option key={tlKey(t)} value={tlKey(t)}>{tlLabel(t)}</option>)}
      </select>
    </div>
    {!levels ? <p className="muted">Loading…</p> :
     !levels.length ? <p className="muted">No games for this combination yet.</p> :
     <>
       <div className="tabs">{levels.map((l) => <button key={l.level} className={l.level === level ? "on" : ""} onClick={() => setLevel(l.level)}>{l.label} ({l.rounds})</button>)}</div>
       {!data ? <p className="muted">Loading…</p> : data.zones.length ? data.zones.map((z) => <Zone key={z.name} z={z} />) : <p className="muted">No zones with 10+ rounds at this level.</p>}
     </>}
  </>;
}
