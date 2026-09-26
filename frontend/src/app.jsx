import { useEffect, useRef, useState } from "react";

const MATCH_TYPES = [
  ["daily", "Daily Challenges"],
  ["challenge", "Challenges"],
  ["duel", "Duels"],
];
const MATCH_TYPE_LABELS = { daily: "Daily", challenge: "Challenge", duel: "Duel" };
const MOVE_TYPE_LABELS = { moving: "Moving", no_move: "No Move", nmpz: "NMPZ" };
const tlKey = (v) => (v == null ? "null" : String(v));
const tlLabel = (v) => (v == null ? "No limit" : `${Math.round(v / 60)} min`);

const api = async (path, opts = {}) => {
  const r = await fetch("/api" + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body && JSON.stringify(opts.body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
};
const place = (g) => [g?.city, g?.state, g?.country].filter(Boolean).join(", ") || "Unknown";
const time = (s) => (s == null ? "—" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
const num = (n) => (n == null ? "—" : n.toLocaleString());
const tone = (score) => `hsl(${Math.max(0, Math.min(1, (score - 2000) / 3000)) * 150} 60% 38%)`;
const flagEmoji = (code) =>
  code ? String.fromCodePoint(...[...code.toUpperCase()].map((c) => 127397 + c.charCodeAt())) : "";
const rowLabel = (g) => {
  const parts = [];
  if (g.time_limit_sec != null) parts.push(`${Math.round(g.time_limit_sec / 60)}min`);
  parts.push(MOVE_TYPE_LABELS[g.move_type] || g.move_type);
  parts.push(g.match_type === "daily" ? "Daily Challenge" : g.match_type === "challenge" ? "Challenge" : "Duel");
  return parts.join(" ");
};

function Spark({ data }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 1), w = 240, h = 40;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - (v / max) * h}`).join(" ");
  return <svg viewBox={`0 0 ${w} ${h}`} width="100%" height="40" role="img" aria-label="Distance per round, oldest to newest"><polyline points={pts} fill="none" stroke="var(--sea)" strokeWidth="1.5" /></svg>;
}

function Dashboard() {
  const [s, setS] = useState(null);
  useEffect(() => {
    const load = () => api("/status").then(setS).catch(() => setS(null));
    load(); const t = setInterval(load, 5000); return () => clearInterval(t);
  }, []);
  const items = s && [
    ["Database", s.db ? "ok" : "bad", s.db ? "Connected" : "Unavailable. Check Postgres and PG_DSN."],
    ["Anki", s.anki ? "ok" : "bad", s.anki ? "AnkiConnect reachable" : "Open Anki with AnkiConnect."],
    ["Ecoregions", { ready: "ok", loading: "wait", error: "bad" }[s.ecoregions], { ready: "Shapefile loaded", loading: "Loading shapefile…", error: "Shapefile failed to load" }[s.ecoregions]],
    ["GeoGuessr cookie", s.cookie ? "ok" : "bad", s.cookie ? "Cookie saved" : "Add one in Settings."],
  ];
  return <><h2>Dashboard</h2>{!s ? <p className="err">Backend unreachable. Is uvicorn running?</p> :
    <div className="grid">{items.map(([t, k, m]) => <div className="card" key={t}><b><span className={`dot ${k}`} />{t}</b><div className="muted">{m}</div></div>)}</div>}</>;
}

function MultiSelectDropdown({ options, selected, onChange, placeholder }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  useEffect(() => {
    const onClick = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false); };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, []);

  const toggle = (value) => {
    onChange(selected.includes(value) ? selected.filter((v) => v !== value) : [...selected, value]);
  };

  const label =
    selected.length === options.length ? "All" :
    selected.length === 0 ? placeholder :
    options.filter((o) => selected.includes(o.value)).map((o) => o.label).join(", ");

  return <div className="msdd" ref={ref}>
    <button type="button" className="msdd-btn" onClick={() => setOpen((o) => !o)}>
      <span>{label}</span><span className="msdd-caret">{open ? "▲" : "▼"}</span>
    </button>
    {open && <div className="msdd-panel">
      {options.map((o) => <label className="msdd-option" key={o.value}>
        <input type="checkbox" checked={selected.includes(o.value)} onChange={() => toggle(o.value)} />
        {o.label}
      </label>)}
    </div>}
  </div>;
}

function Sync() {
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

  return <><h2>Sync games</h2>
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
    {games.map((g) => <div className="card" key={g.game_id} style={{ marginTop: 12 }}>
      <b>{g.map_name}</b>{g.done ? <span className="muted"> — {num(g.done.total_score)} / 25,000, avg {num(g.done.avg_distance_km)} km</span> : <span className="muted"> — processing…</span>}
      {g.rounds.map((r) => <div className="round" key={r.round_number}>
        <span className="muted">{r.round_number}</span>
        <div>{place(r.real_geo)}<div className="muted">You: {place(r.guess_geo)}</div></div>
        <div style={{ textAlign: "right" }}><b style={{ color: tone(r.score) }}>{num(r.score)}</b><div className="muted">{num(r.distance_km)} km · {time(r.time_sec)} · {r.steps} steps</div></div>
      </div>)}
    </div>)}
    {!running && !events.length && <p className="muted">Nothing synced in this session yet.</p>}</>;
}

function Zone({ z }) {
  const title = z.name === "_global_" ? "Global" : z.name;
  const ci = z.ci_lo != null ? ` (${num(z.ci_lo)}–${num(z.ci_hi)} km)` : "";
  return <div className="card">
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
      <b style={{ fontSize: 18 }}>{title}</b><span className="muted">{z.total} rounds</span></div>
    <div className="stats">
      <div><span className="muted">Current level</span><b className="score" style={{ color: tone(z.score_high ?? 0) }}>{z.level_label} {z.level_arrow}</b><span className="muted">{ci}</span></div>
      <div><span className="muted">Worst rounds</span><b>{z.p90_label} {z.p90_arrow}</b><span className="muted">worst 10% ≈ {num(z.p90_now_km)} km</span></div>
      <div><span className="muted">Consistency</span><b>{z.cons_label} {z.cons_arrow}</b><span className="muted">typical ±{num(z.std_now_km)} km</span></div>
    </div>
    <Spark data={z.series} />
    {z.confusions[0] && <div className="conf"><b>Key confusion:</b> {z.confusions[0].real} → {z.confusions[0].guess} ({z.confusions[0].freq}×, ~{num(z.confusions[0].avg_km)} km)</div>}
    {z.child_confusions.length > 0 && <div className="conf"><b>Confusions ({z.child_level}):</b>
      {z.child_confusions.map((c, i) => <div key={i}>• {c.real} → {c.guess} ({c.freq}×, ~{num(c.avg_km)} km)</div>)}</div>}
  </div>;
}

function Analysis() {
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

function History() {
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

function Settings() {
  const [s, setS] = useState(null);
  const [cookie, setCookie] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api("/settings").then(setS); }, []);
  const run = async (fn, ok) => { try { await fn(); setMsg(ok); api("/settings").then(setS); } catch (e) { setMsg(e.message); } };
  if (!s) return null;
  return <><h2>Settings</h2>
    <label htmlFor="ck">GeoGuessr cookie (_ncfa) {s.cookie && <span className="muted">— saved</span>}</label>
    <input id="ck" type="password" value={cookie} onChange={(e) => setCookie(e.target.value)} placeholder="Paste cookie value" />
    <p><button className="btn" disabled={!cookie} onClick={() => run(() => api("/settings/cookie", { method: "PUT", body: { cookie } }), "Cookie saved").then(() => setCookie(""))}>Save cookie</button>{" "}
      <button className="btn ghost" onClick={() => run(() => api("/settings/cookie/refresh", { method: "POST" }), "Cookie refreshed from browser")}>Refresh from browser</button></p>
    <label htmlFor="uid">GeoGuessr user ID</label>
    <input id="uid" defaultValue={s.user_id} onBlur={(e) => run(() => api("/settings/user", { method: "PUT", body: { user_id: e.target.value } }), "User ID saved")} />
    <label htmlFor="lang">Language</label>
    <select id="lang" value={s.lang} onChange={(e) => run(() => api("/settings/lang", { method: "PUT", body: { lang: e.target.value } }), "Language changed")}>
      <option value="en">English</option><option value="es">Español</option></select>
    {msg && <p className="muted">{msg}</p>}</>;
}

const PAGES = { Dashboard, Sync, Analysis, History, Settings };

export default function App() {
  const [page, setPage] = useState("Dashboard");
  const Page = PAGES[page];
  return <div className="app">
    <nav className="rail"><h1>GeoGuessr Trainer</h1>
      {Object.keys(PAGES).map((p) => <button key={p} className={p === page ? "on" : ""} onClick={() => setPage(p)}>{p}</button>)}</nav>
    <main><Page /></main>
  </div>;
}