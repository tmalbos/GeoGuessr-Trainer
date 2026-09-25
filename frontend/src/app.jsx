import { useEffect, useRef, useState } from "react";

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

function Sync() {
  const [events, setEvents] = useState([]);
  const [running, setRunning] = useState(false);
  const es = useRef(null);
  const [err, setErr] = useState("");

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
    try { await api("/sync", { method: "POST" }); listen(); } catch (e) { setErr(e.message); }
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
    <p><button className="btn" onClick={start} disabled={running}>{running ? "Syncing…" : "Sync new games"}</button></p>
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
  const [levels, setLevels] = useState(null);
  const [level, setLevel] = useState(null);
  const [data, setData] = useState(null);
  useEffect(() => { api("/analysis/levels").then((l) => { setLevels(l); setLevel(l[0]?.level); }).catch(() => setLevels([])); }, []);
  useEffect(() => { if (level) { setData(null); api(`/analysis/${level}`).then(setData); } }, [level]);
  if (!levels) return <p className="muted">Loading…</p>;
  if (!levels.length) return <><h2>Analysis</h2><p className="muted">Sync at least 10 rounds to unlock analysis.</p></>;
  return <><h2>Analysis</h2>
    <div className="tabs">{levels.map((l) => <button key={l.level} className={l.level === level ? "on" : ""} onClick={() => setLevel(l.level)}>{l.label} ({l.rounds})</button>)}</div>
    {!data ? <p className="muted">Loading…</p> : data.zones.length ? data.zones.map((z) => <Zone key={z.name} z={z} />) : <p className="muted">No zones with 10+ rounds at this level.</p>}</>;
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

const PAGES = { Dashboard, Sync, Analysis, Settings };

export default function App() {
  const [page, setPage] = useState("Dashboard");
  const Page = PAGES[page];
  return <div className="app">
    <nav className="rail"><h1>GeoGuessr Trainer</h1>
      {Object.keys(PAGES).map((p) => <button key={p} className={p === page ? "on" : ""} onClick={() => setPage(p)}>{p}</button>)}</nav>
    <main><Page /></main>
  </div>;
}