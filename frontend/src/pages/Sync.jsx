import { useEffect, useRef, useState } from "react";
import MultiSelectDropdown from "../components/MultiSelectDropdown.jsx";
import { api } from "../lib/api.js";
import { num, time, tone } from "../lib/format.js";

const MATCH_TYPES = [
  ["daily", "Daily Challenges"],
  ["challenge", "Challenges"],
  ["duel", "Duels"],
];
const place = (g) => [g?.city, g?.state, g?.country].filter(Boolean).join(", ") || "Unknown";

export default function Sync() {
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);
  const es = useRef(null);
  const [err, setErr] = useState("");
  const [types, setTypes] = useState(MATCH_TYPES.map(([k]) => k));

  const listen = () => {
    es.current?.close(); setEvents([]); setRunning(true);
    const src = (es.current = new EventSource("/api/sync/events"));
    src.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      if (ev.type === "done") { src.close(); setRunning(false); }
      setEvents((p) => [...p, ev]);
    };
    src.onerror = () => { src.close(); setRunning(false); };
  };
  useEffect(() => { listen(); return () => es.current?.close(); }, []);

  const start = async () => {
    setErr("");
    try { await api("/sync", { method: "POST", body: { match_types: types } }); listen(); }
    catch (e) { setErr(e.message); }
  };

  const games = [];
  const logs = [];
  events.forEach((e) => {
    if (e.type === "game_started") games.push({ ...e.data, rounds: [] });
    else if (e.type === "round_result") games.at(-1)?.rounds.push(e.data);
    else if (e.type === "game_done") Object.assign(games.at(-1) || {}, { done: e.data });
    else if (["log", "error"].includes(e.type)) logs.push({ level: e.type === "error" ? "error" : e.data.level, msg: e.data.message });
    else if (e.type === "anki_errors") e.data.errors.forEach((m) => logs.push({ level: "error", msg: `Anki: ${m}` }));
    else if (e.type === "feed") logs.push({ level: "info", msg: `${e.data.found} games in feed, ${e.data.new} new.` });
  });

  return <div className="sync-page">
    <div className="sync-head">
      <h2>Sync games</h2>
      <label htmlFor="match-types">Match types</label>
      <MultiSelectDropdown
        options={MATCH_TYPES.map(([value, label]) => ({ value, label }))}
        selected={types}
        onChange={setTypes}
        placeholder="Select match types"
      />
      <p><button className="btn" onClick={start} disabled={running || !types.length}>{running ? "Syncing…" : "Sync new games"}</button></p>
      {err && <p className="err">{err}</p>}
      {logs.map((l, i) => <div key={i} className={`log ${l.level}`}>{l.msg}</div>)}
    </div>
    {[...games].reverse().map((g) => <div className="card" key={g.game_id} style={{ marginTop: 12 }}>
      <b>{g.map_name}</b>{g.done ? <span className="muted"> — {num(g.done.total_score)} / 25,000, avg {num(g.done.avg_distance_km)} km</span> : <span className="muted"> — processing…</span>}
      {g.rounds.map((r) => <div className="round" key={r.round_number}>
        <span className="muted">{r.round_number}</span>
        <div>{place(r.real_geo)}<div className="muted">You: {place(r.guess_geo)}</div></div>
        <div style={{ textAlign: "right" }}><b style={{ color: tone(r.score) }}>{num(r.score)}</b><div className="muted">{num(r.distance_km)} km · {time(r.time_sec)} · {r.steps} steps</div></div>
      </div>)}
    </div>)}
    {!running && !events.length && <p className="muted" style={{ textAlign: "center" }}>Nothing synced in this session yet.</p>}
  </div>;
}
