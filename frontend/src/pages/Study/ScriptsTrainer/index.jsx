import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../../../lib/api.js";
import { time, tlLabel } from "../../../lib/format.js";
import { defaultNormalizeAnswer, loadPlaces } from "./geonames.js";
import LANGUAGES from "./languages/index.js";
import "./scripts.css";

// Seconds. 0 = no timer (free practice, nothing is saved).
const LIMITS = [0, 60, 180, 300, 600, 900];
const limitLabel = (s) => (s ? tlLabel(s) : "No timer");
const fmtDate = (d) => d.replace("T", " ").slice(0, 16);

const shuffle = (arr) => {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
};

export default function ScriptsTrainer({ onExit }) {
  const [screen, setScreen] = useState("lang"); // lang | menu | game | ref | summary
  const [lang, setLang] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | loading | ready | error
  const [msg, setMsg] = useState("");
  const [current, setCurrent] = useState(null);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState(null); // null | ok | bad
  const [stats, setStats] = useState({ ok: 0, total: 0, skipped: 0 });
  const [limit, setLimit] = useState(0);
  const [left, setLeft] = useState(0);
  const [summary, setSummary] = useState(null);
  const [saveErr, setSaveErr] = useState("");
  const [history, setHistory] = useState(null);
  const cache = useRef({});
  const queue = useRef([]);
  const timer = useRef(null);
  const inputRef = useRef(null);
  const extraRef = useRef(null);
  const statsRef = useRef(stats);
  statsRef.current = stats;

  const nextPlace = () => {
    clearTimeout(timer.current);
    if (!queue.current.length) queue.current = shuffle([...cache.current[lang.id]]);
    setCurrent(queue.current.pop());
    setAnswer("");
    setResult(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const start = async () => {
    setStats({ ok: 0, total: 0, skipped: 0 });
    setLeft(limit);
    setScreen("game");
    if (!cache.current[lang.id]) {
      setStatus("loading");
      try {
        cache.current[lang.id] = await loadPlaces(lang, (c) => setMsg(`Reading ${c}.txt…`));
      } catch (e) {
        setMsg(e.message);
        setStatus("error");
        return;
      }
    }
    setStatus("ready");
    queue.current = [];
    nextPlace();
  };

  const finish = () => {
    clearTimeout(timer.current);
    const s = statsRef.current;
    const rec = { script: lang.id, good: s.ok, bad: s.total - s.ok, skipped: s.skipped, time_limit_sec: limit };
    setSummary(rec);
    setSaveErr("");
    setScreen("summary");
    api("/study/scripts/sessions", { method: "POST", body: rec }).catch((e) => setSaveErr(e.message));
  };

  const check = () => {
    if (!current) return;
    if (result) return nextPlace();
    if (!answer.trim()) return;
    const norm = lang.normalizeAnswer || defaultNormalizeAnswer;
    const right = norm(answer) === norm(current.transliteration);
    setStats((s) => ({ ...s, ok: s.ok + (right ? 1 : 0), total: s.total + 1 }));
    setResult(right ? "ok" : "bad");
    if (right) timer.current = setTimeout(nextPlace, 450);
  };

  const skip = () => {
    if (!result) setStats((s) => ({ ...s, skipped: s.skipped + 1 }));
    nextPlace();
  };

  useEffect(() => {
    if (screen !== "game") return;
    const onKey = (e) => { if (e.key === "Escape") setScreen("menu"); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [screen]);

  // Countdown: starts once the places are loaded; leaving the game screen cancels it.
  useEffect(() => {
    if (screen !== "game" || status !== "ready" || !limit) return;
    const endAt = Date.now() + limit * 1000;
    const id = setInterval(() => {
      const rem = Math.max(0, Math.ceil((endAt - Date.now()) / 1000));
      setLeft(rem);
      if (rem === 0) { clearInterval(id); finish(); }
    }, 250);
    return () => clearInterval(id);
  }, [screen, status]);

  // Past timed sessions for the selected script, shown under its menu.
  useEffect(() => {
    if (screen !== "menu" || !lang) return;
    setHistory(null);
    api(`/study/scripts/sessions?script=${lang.id}`).then(setHistory).catch(() => setHistory([]));
  }, [screen, lang]);

  useEffect(() => {
    if (screen === "ref" && extraRef.current) {
      extraRef.current.innerHTML = "";
      lang.renderExtraReference?.(extraRef.current);
    }
  }, [screen, lang]);

  useEffect(() => () => clearTimeout(timer.current), []);

  const pick = (l) => { setLang(l); setStatus("idle"); setScreen("menu"); };
  const style = lang && { "--gfont": lang.fontFamily, "--wordsize": lang.wordSize };

  const cell = (p) => p
    ? <><td className="g" lang={lang.id} dir={lang.dir}>{p[0]}</td><td>{p[1]}</td></>
    : <><td /><td /></>;
  const pairRows = (pairs) => {
    const half = Math.ceil(pairs.length / 2);
    return Array.from({ length: half }, (_, i) =>
      <tr key={i}><Fragment>{cell(pairs[i])}{cell(pairs[i + half])}</Fragment></tr>);
  };

  if (screen === "lang") return <div className="sc">
    <button className="btn ghost" onClick={onExit}>← Study</button>
    <h2>Scripts trainer</h2>
    <p className="muted">Practice reading real place names. Pick a language.</p>
    <div className="sc-list">{LANGUAGES.map((l) =>
      <button key={l.id} className="sc-lang" onClick={() => pick(l)}>
        <span><b className="sc-name">{l.name}</b><span className="muted">{l.script}</span></span>
        <span className="sc-right">
          <span className={`sc-tag ${l.difficulty.level}`}>{l.difficulty.label}</span>
          <span className="sc-native" lang={l.id} dir={l.dir} style={{ fontFamily: l.fontFamily }}>{l.native}</span>
        </span>
      </button>)}</div>
  </div>;

  if (screen === "menu") return <div className="sc" style={style}>
    <h2>{lang.name} <span className="sc-native" lang={lang.id} dir={lang.dir} style={{ fontFamily: lang.fontFamily }}>{lang.native}</span> <span className={`sc-tag ${lang.difficulty.level}`}>{lang.difficulty.label}</span></h2>
    <p className="muted">{lang.lead}</p>
    <div className="sc-setup">
      <label htmlFor="sc-limit">Time limit</label>
      <select id="sc-limit" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
        {LIMITS.map((s) => <option key={s} value={s}>{limitLabel(s)}</option>)}
      </select>
      <p className="muted">{limit ? "Timed sessions are saved to your history." : "Free practice is not saved."}</p>
    </div>
    <div className="sc-list">
      <button className="btn" onClick={start}>Start practicing</button>
      <button className="btn ghost" onClick={() => setScreen("ref")}>Reference table</button>
      <button className="btn ghost" onClick={() => setScreen("lang")}>← Change language</button>
    </div>
    <div className="sc-hist">
      <h3>History</h3>
      {!history ? <p className="muted">Loading…</p> : !history.length ? <p className="muted">No timed sessions yet.</p> :
        <table>
          <thead><tr><th>Date</th><th>Limit</th><th>Good</th><th>Bad</th><th>Skipped</th></tr></thead>
          <tbody>{history.map((h) => <tr key={h.date}>
            <td>{fmtDate(h.date)}</td><td>{limitLabel(h.time_limit_sec)}</td>
            <td>{h.good}</td><td>{h.bad}</td><td>{h.skipped}</td>
          </tr>)}</tbody>
        </table>}
    </div>
  </div>;

  if (screen === "ref") return <div className="sc" style={style}>
    <button className="btn ghost" onClick={() => setScreen("menu")}>← Menu</button>
    <h2>Reference</h2>
    <p className="muted">{lang.refLead}</p>
    <table>
      <thead><tr><th>Letter</th><th>Reads</th><th>Letter</th><th>Reads</th></tr></thead>
      <tbody>{pairRows(lang.pairs)}</tbody>
    </table>
    <div ref={extraRef} className="sc-extra" />
    {lang.extra.length > 0 && <>
      <h3>{lang.extraTitle}</h3>
      <table><tbody>{pairRows(lang.extra)}</tbody></table>
    </>}
  </div>;

  if (screen === "summary") return <div className="sc" style={style}>
    <h2>Time's up</h2>
    <p className="muted">{lang.name} · {limitLabel(summary.time_limit_sec)}</p>
    <div className="card">
      <div className="stats">
        <div><span className="muted">Good</span><b className="score">{summary.good}</b></div>
        <div><span className="muted">Bad</span><b className="score">{summary.bad}</b></div>
        <div><span className="muted">Skipped</span><b className="score">{summary.skipped}</b></div>
      </div>
      {saveErr && <p className="err">Could not save this session: {saveErr}</p>}
    </div>
    <div className="sc-row" style={{ justifyContent: "flex-start" }}>
      <button className="btn" onClick={start}>Play again</button>
      <button className="btn ghost" onClick={() => setScreen("menu")}>← Menu</button>
    </div>
  </div>;

  return <div className="sc" style={style}>
    <div className="sc-top">
      <button className="btn ghost" onClick={() => setScreen("menu")}>← Menu</button>
      {limit > 0 && status === "ready" && <span className={`sc-timer ${left <= 10 ? "low" : ""}`} role="timer">{time(left)}</span>}
      <span className="muted" aria-live="polite">{stats.total ? `${stats.ok} of ${stats.total} correct` : ""}</span>
    </div>
    {status === "loading" && <p className="muted">{msg}</p>}
    {status === "error" && <p className="err">{msg}</p>}
    {status === "ready" && current && <div className="card">
      <div className="sc-word" lang={lang.id} dir={lang.dir}>{current.name}</div>
      <input ref={inputRef} className={`sc-answer ${result || ""}`} value={answer}
        disabled={result === "bad"} autoComplete="off" autoCapitalize="off" spellCheck={false}
        placeholder="Type the transliteration" aria-label="Transliteration"
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); check(); } }} />
      <div className="sc-feedback" aria-live="polite">
        {result === "ok" && "✓ Correct"}
        {result === "bad" && <>Answer: <span className="sc-expected">{current.transliteration}</span></>}
      </div>
      <div className="sc-row">
        <button className="btn" onClick={check}>{result === "bad" ? "Next" : "Check"}</button>
        <button className="btn ghost" onClick={skip}>Skip</button>
      </div>
      <p className="muted" style={{ textAlign: "center" }}>Enter to check · Esc for menu</p>
    </div>}
  </div>;
}
