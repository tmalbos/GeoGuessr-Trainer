import { useEffect, useState } from "react";
import { api } from "../lib/api.js";
import { MATCH_TYPE_LABELS, MOVE_TYPE_LABELS, num, time, tlKey, tlLabel, tone } from "../lib/format.js";

const flagEmoji = (code) =>
  code ? String.fromCodePoint(...[...code.toUpperCase()].map((c) => 127397 + c.charCodeAt())) : "";
const rowLabel = (g) => {
  const parts = [];
  if (g.time_limit_sec != null) parts.push(`${Math.round(g.time_limit_sec / 60)}min`);
  parts.push(MOVE_TYPE_LABELS[g.move_type] || g.move_type);
  parts.push(g.match_type === "daily" ? "Daily Challenge" : g.match_type === "challenge" ? "Challenge" : "Duel");
  return parts.join(" ");
};

export default function History() {
  const [filterOpts, setFilterOpts] = useState(null);
  const [matchType, setMatchType] = useState("");
  const [moveType, setMoveType] = useState("");
  const [timeLimit, setTimeLimit] = useState("any");
  const [minScore, setMinScore] = useState("");
  const [maxScore, setMaxScore] = useState("");
  const [sortBy, setSortBy] = useState("date");
  const [sortDir, setSortDir] = useState("desc");
  const [games, setGames] = useState(null);

  useEffect(() => { api("/analysis/filters").then(setFilterOpts); }, []);

  useEffect(() => {
    if (!filterOpts) return;
    const qs = new URLSearchParams();
    if (matchType) qs.set("match_type", matchType);
    if (moveType) qs.set("move_type", moveType);
    qs.set("time_limit", timeLimit);
    if (minScore !== "") qs.set("min_score", minScore);
    if (maxScore !== "") qs.set("max_score", maxScore);
    qs.set("sort_by", sortBy);
    qs.set("sort_dir", sortDir);
    setGames(null);
    api(`/history?${qs}`).then(setGames).catch(() => setGames([]));
  }, [filterOpts, matchType, moveType, timeLimit, minScore, maxScore, sortBy, sortDir]);

  if (!filterOpts) return <><h2>History</h2><p className="muted">Loading…</p></>;

  return <><h2>History</h2>
    <div className="hist-filters">
      <div><label>Game type</label><select value={matchType} onChange={(e) => setMatchType(e.target.value)}>
        <option value="">All</option>
        {filterOpts.match_types.map((m) => <option key={m} value={m}>{MATCH_TYPE_LABELS[m] || m}</option>)}
      </select></div>
      <div><label>Game mode</label><select value={moveType} onChange={(e) => setMoveType(e.target.value)}>
        <option value="">All</option>
        {filterOpts.move_types.map((m) => <option key={m} value={m}>{MOVE_TYPE_LABELS[m] || m}</option>)}
      </select></div>
      <div><label>Time limit</label><select value={timeLimit} onChange={(e) => setTimeLimit(e.target.value)}>
        <option value="any">All</option>
        {filterOpts.time_limits.map((t) => <option key={tlKey(t)} value={tlKey(t)}>{tlLabel(t)}</option>)}
      </select></div>
      <div><label>Min score</label><input type="number" value={minScore} onChange={(e) => setMinScore(e.target.value)} style={{ width: 90 }} /></div>
      <div><label>Max score</label><input type="number" value={maxScore} onChange={(e) => setMaxScore(e.target.value)} style={{ width: 90 }} /></div>
      <div><label>Sort by</label><select value={sortBy} onChange={(e) => setSortBy(e.target.value)}>
        <option value="date">Date</option>
        <option value="total_score">Total score</option>
        <option value="total_steps">Total steps</option>
        <option value="total_time">Total time</option>
      </select></div>
      <div><label>Direction</label><select value={sortDir} onChange={(e) => setSortDir(e.target.value)}>
        <option value="desc">Desc</option>
        <option value="asc">Asc</option>
      </select></div>
    </div>
    {!games ? <p className="muted">Loading…</p> : !games.length ? <p className="muted">No games match these filters.</p> :
      games.map((g) => <div className="hist-row" key={`${g.challenge_token}-${g.game_id}`}>
        <div className="hist-meta"><b>{rowLabel(g)}</b><div className="muted">{g.played_at?.slice(0, 10)}</div></div>
        {g.rounds.map((r) => <div className="hist-round" key={r.round_number}>
          <div style={{ color: tone(r.score) }}><b>{num(r.score)}</b></div>
          <div className="muted">{r.steps} steps</div>
          <div className="muted">{time(r.time_sec)}</div>
          <div className="flag">{flagEmoji(r.country_code)}</div>
        </div>)}
        <div className="hist-total">
          <div>{num(g.total_score)}</div>
          <div className="muted">{g.total_steps} steps</div>
          <div className="muted">{time(g.total_time_sec)}</div>
        </div>
      </div>)}
  </>;
}
