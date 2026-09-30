import { useEffect, useRef, useState } from "react";
import { api } from "../lib/api.js";
import "./sync-button.css";

const TYPES = [
  ["daily", "Daily challenges"],
  ["challenge", "Challenges"],
  ["duel", "Duels"],
];

const EMPTY = { saved: 0, found: 0, current: "", errors: [], finished: false };

function reduce(r, ev) {
  switch (ev.type) {
    case "feed": return { ...r, found: r.found + (ev.data.new ?? 0) };
    case "game_started": return { ...r, current: ev.data.map_name || "" };
    case "game_done": return { ...r, saved: r.saved + 1 };
    case "error": return { ...r, errors: [...r.errors, ev.data.message] };
    case "log": return ev.data.level === "error" ? { ...r, errors: [...r.errors, ev.data.message] } : r;
    case "done": return { ...r, finished: true, current: "" };
    default: return r;
  }
}

/** Split button: main part syncs, caret opens the match-type picker.
 *  onGameSaved fires for every game stored; onFinished once when the job ends. */
export default function SyncButton({ onGameSaved, onFinished }) {
  const [types, setTypes] = useState(TYPES.map(([k]) => k));
  const [menu, setMenu] = useState(false);
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);
  const [err, setErr] = useState("");
  const es = useRef(null);
  const box = useRef(null);
  const cbs = useRef({});
  cbs.current = { onGameSaved, onFinished };

  const listen = () => {
    es.current?.close();
    setRunning(true);
    setReport(EMPTY);
    const src = (es.current = new EventSource("/api/sync/events"));
    src.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      setReport((r) => reduce(r || EMPTY, ev));
      if (ev.type === "game_done") cbs.current.onGameSaved?.();
      if (ev.type === "done") { src.close(); setRunning(false); cbs.current.onFinished?.(); }
    };
    src.onerror = () => { src.close(); setRunning(false); };
  };

  // Re-attach if a sync is still running (e.g. user left the page and came back).
  useEffect(() => {
    api("/status").then((s) => { if (s.sync_running) listen(); }).catch(() => {});
    return () => es.current?.close();
  }, []);

  useEffect(() => {
    if (!menu) return;
    const onDown = (e) => { if (box.current && !box.current.contains(e.target)) setMenu(false); };
    const onKey = (e) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menu]);

  const start = async () => {
    setMenu(false); setErr("");
    try { await api("/sync", { method: "POST", body: { match_types: types } }); listen(); }
    catch (e) { setReport(null); setErr(e.message); }
  };

  const toggle = (k) => setTypes((t) => (t.includes(k) ? t.filter((x) => x !== k) : [...t, k]));

  let status = null;
  if (err) status = { kind: "err", text: err };
  else if (report) {
    const firstErr = report.errors[0]?.split("\n")[0];
    if (running) status = { kind: "", text: report.current ? `Syncing ${report.current}… ${report.saved} saved` : `Syncing… ${report.saved} saved` };
    else if (firstErr) status = { kind: "err", text: firstErr };
    else if (report.finished) status = { kind: "ok", text: report.saved ? `Synced ${report.saved} new game${report.saved === 1 ? "" : "s"}` : "Already up to date" };
  }

  return <div className="sync-area">
    {status && <div className={`sync-status ${status.kind}`} role="status" title={report?.errors?.[0] || status.text}>
      <span>{status.text}</span>
      {!running && <button type="button" aria-label="Dismiss" onClick={() => { setReport(null); setErr(""); }}>×</button>}
    </div>}
    <div className="split" ref={box}>
      <button type="button" className="btn split-main" onClick={start} disabled={running || !types.length}>
        <span className={`split-icon ${running ? "spin" : ""}`} aria-hidden="true">↻</span>
        {running ? "Syncing…" : "Sync games"}
        {!running && types.length < TYPES.length && types.length > 0 && <span className="split-badge">{types.length}/{TYPES.length}</span>}
      </button>
      <button type="button" className="btn split-caret" aria-haspopup="true" aria-expanded={menu}
        aria-label="Choose what to sync" disabled={running} onClick={() => setMenu((o) => !o)}>{menu ? "▲" : "▼"}</button>
      {menu && <div className="split-menu" role="group" aria-label="Match types to sync">
        <h4>Sync these</h4>
        {TYPES.map(([k, label]) => <label className="split-option" key={k}>
          <input type="checkbox" checked={types.includes(k)} onChange={() => toggle(k)} />{label}
        </label>)}
        {!types.length && <p className="split-hint">Pick at least one type.</p>}
      </div>}
    </div>
  </div>;
}
