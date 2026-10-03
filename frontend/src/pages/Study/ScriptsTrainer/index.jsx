import { Fragment, useEffect, useRef, useState } from "react";
import ConfirmDialog from "../../../components/ConfirmDialog.jsx";
import Flag from "../../../components/Flag.jsx";
import { Skeleton } from "../../../components/Skeleton.jsx";
import { api, errorText } from "../../../lib/api.js";
import { time, tlLabel } from "../../../lib/format.js";
import { defaultNormalizeAnswer, loadPlaces } from "./geonames.js";
import LANGUAGES from "./languages/index.js";
import "./scripts.css";

// Seconds. 0 = no timer (free practice, nothing is saved).
const LIMITS = [0, 60, 180, 300, 600, 900];
const limitLabel = (s) => (s ? tlLabel(s) : "No timer");
const fmtDate = (d) => d.replace("T", " ").slice(0, 16);
const accuracy = (s) => (s.good + s.bad ? Math.round((s.good / (s.good + s.bad)) * 100) : 0);

const shuffle = (arr) => {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
};

// Which characters of each string are part of the longest common subsequence (so we can mark the misses).
function diffChars(a, b) {
  const x = [...a], y = [...b];
  const dp = Array.from({ length: x.length + 1 }, () => new Array(y.length + 1).fill(0));
  for (let i = 1; i <= x.length; i++)
    for (let j = 1; j <= y.length; j++)
      dp[i][j] = x[i - 1] === y[j - 1] ? dp[i - 1][j - 1] + 1 : Math.max(dp[i - 1][j], dp[i][j - 1]);
  const mx = new Array(x.length).fill(false), my = new Array(y.length).fill(false);
  let i = x.length, j = y.length;
  while (i > 0 && j > 0) {
    if (x[i - 1] === y[j - 1]) { mx[i - 1] = my[j - 1] = true; i--; j--; }
    else if (dp[i - 1][j] >= dp[i][j - 1]) i--;
    else j--;
  }
  return [x.map((ch, k) => ({ ch, ok: mx[k] })), y.map((ch, k) => ({ ch, ok: my[k] }))];
}
const Marked = ({ chars }) => <>{chars.map((c, i) => <span key={i} className={c.ok ? "" : "sc-x"}>{c.ch}</span>)}</>;

const SavedIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;
const NotSavedIcon = () => <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M5 12h14" /></svg>;

// Accuracy of each timed session, oldest to newest.
function AccuracyChart({ sessions }) {
  const pts = [...sessions].reverse().slice(-30).map(accuracy);
  if (pts.length < 2) return null;
  const w = 560, h = 70, px = 10;
  const step = (w - 2 * px) / (pts.length - 1);
  const xy = pts.map((v, i) => [px + i * step, h - 8 - (v / 100) * (h - 16)]);
  return <svg className="sc-chart" viewBox={`0 0 ${w} ${h}`} role="img" aria-label={`Accuracy over your last ${pts.length} sessions, most recent ${pts.at(-1)} percent`}>
    <polyline points={xy.map((p) => p.join(",")).join(" ")} fill="none" stroke="var(--sea-hi)" strokeWidth="2" strokeLinejoin="round" />
    {xy.map(([x, y], i) => <circle key={i} cx={x} cy={y} r="3.5" fill="var(--sea-hi)"><title>{pts[i]}%</title></circle>)}
  </svg>;
}

export default function ScriptsTrainer({ onExit }) {
  const [screen, setScreen] = useState("lang"); // lang | menu | game | ref | summary
  const [lang, setLang] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | loading | ready | error
  const [msg, setMsg] = useState("");
  const [progress, setProgress] = useState({ code: "", index: 0, total: 1 });
  const [current, setCurrent] = useState(null);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState(null); // null | ok | bad
  const [diff, setDiff] = useState(null);
  const [stats, setStats] = useState({ ok: 0, total: 0, skipped: 0 });
  const [limit, setLimit] = useState(0);
  const [left, setLeft] = useState(0);
  const [summary, setSummary] = useState(null);
  const [saveErr, setSaveErr] = useState("");
  const [history, setHistory] = useState(null);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const cache = useRef({});
  const queue = useRef([]);
  const timer = useRef(null);
  const inputRef = useRef(null);
  const extraRef = useRef(null);
  const statsRef = useRef(stats);
  statsRef.current = stats;

  const norm = (s) => (lang.normalizeAnswer || defaultNormalizeAnswer)(s);

  const nextPlace = () => {
    clearTimeout(timer.current);
    if (!queue.current.length) queue.current = shuffle([...cache.current[lang.id]]);
    setCurrent(queue.current.pop());
    setAnswer("");
    setResult(null);
    setDiff(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const start = async () => {
    setStats({ ok: 0, total: 0, skipped: 0 });
    setLeft(limit);
    setConfirmLeave(false);
    setScreen("game");
    if (!cache.current[lang.id]) {
      setStatus("loading");
      setProgress({ code: lang.countries[0], index: 0, total: lang.countries.length });
      try {
        cache.current[lang.id] = await loadPlaces(lang, setProgress);
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

  const finish = async () => {
    clearTimeout(timer.current);
    setConfirmLeave(false);
    const s = statsRef.current;
    const rec = { script: lang.id, good: s.ok, bad: s.total - s.ok, skipped: s.skipped, time_limit_sec: limit };
    setSaveErr("");
    setSummary({ ...rec, best: null, isBest: false });
    setScreen("summary");
    let best = 0;
    try {
      const prev = await api(`/study/scripts/sessions?script=${lang.id}`);
      best = Math.max(0, ...prev.filter((p) => p.time_limit_sec === limit).map((p) => p.good));
    } catch { /* the personal-best line is optional */ }
    setSummary({ ...rec, best, isBest: rec.good > 0 && rec.good > best });
    api("/study/scripts/sessions", { method: "POST", body: rec }).catch((e) => setSaveErr(errorText(e)));
  };

  const check = () => {
    if (!current) return;
    if (result) return nextPlace();
    if (!answer.trim()) return;
    const right = norm(answer) === norm(current.transliteration);
    setStats((s) => ({ ...s, ok: s.ok + (right ? 1 : 0), total: s.total + 1 }));
    setResult(right ? "ok" : "bad");
    if (right) timer.current = setTimeout(nextPlace, 450);
    else setDiff(diffChars(norm(answer), norm(current.transliteration)));
  };

  const skip = () => {
    if (!result) setStats((s) => ({ ...s, skipped: s.skipped + 1 }));
    nextPlace();
  };

  // Leaving a timed session throws it away, so ask first (unless nothing was answered yet).
  const leave = () => {
    if (limit > 0 && status === "ready" && stats.total + stats.skipped > 0) setConfirmLeave(true);
    else setScreen("menu");
  };
  const leaveRef = useRef(leave);
  leaveRef.current = leave;

  useEffect(() => {
    if (screen !== "game") return;
    const onKey = (e) => { if (e.key === "Escape") leaveRef.current(); };
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

  if (screen === "menu") {
    const bestFor = {};
    for (const h of history ?? []) bestFor[h.time_limit_sec] = Math.max(bestFor[h.time_limit_sec] ?? 0, h.good);
    return <div className="sc" style={style}>
      <h2>{lang.name} <span className="sc-native" lang={lang.id} dir={lang.dir} style={{ fontFamily: lang.fontFamily }}>{lang.native}</span> <span className={`sc-tag ${lang.difficulty.level}`}>{lang.difficulty.label}</span></h2>
      <p className="muted">{lang.lead}</p>
      <div className="sc-setup">
        <label id="sc-limit-l">Time limit</label>
        <div className="tabs" role="radiogroup" aria-labelledby="sc-limit-l">
          {LIMITS.map((s) => <button key={s} type="button" role="radio" aria-checked={limit === s} className={limit === s ? "on" : ""} onClick={() => setLimit(s)}>{limitLabel(s)}</button>)}
        </div>
        <span className={`badge ${limit ? "on" : ""}`} title={limit ? "Timed sessions are saved to your history" : "Free practice isn't saved"}>
          {limit ? <><SavedIcon />Saved</> : <><NotSavedIcon />Not saved</>}
        </span>
      </div>
      <div className="sc-list">
        <button className="btn" onClick={start}>Start practicing</button>
        <button className="btn ghost" onClick={() => setScreen("ref")}>Reference table</button>
        <button className="btn ghost" onClick={() => setScreen("lang")}>← Change language</button>
      </div>
      <div className="sc-hist">
        <h3>History</h3>
        {!history ? <div className="skel-rows"><Skeleton h={70} r={8} /><Skeleton h={34} r={6} /><Skeleton h={34} r={6} /></div> : !history.length ? <p className="muted">No timed sessions yet.</p> : <>
          <AccuracyChart sessions={history} />
          <table>
            <thead><tr><th>Date</th><th>Limit</th><th>Good</th><th>Bad</th><th>Skipped</th><th>Accuracy</th></tr></thead>
            <tbody>{history.map((h) => <tr key={h.date}>
              <td>{fmtDate(h.date)}</td><td>{limitLabel(h.time_limit_sec)}</td>
              <td>{h.good}{h.good > 0 && h.good === bestFor[h.time_limit_sec] && <span title="Best for this time limit" aria-label="Best for this time limit"> ★</span>}</td>
              <td>{h.bad}</td><td>{h.skipped}</td>
              <td><span className="acc"><span className="bar"><i style={{ width: `${accuracy(h)}%` }} /></span>{accuracy(h)}%</span></td>
            </tr>)}</tbody>
          </table></>}
      </div>
    </div>;
  }

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
      <div className="sc-acc"><b>{accuracy(summary)}%</b><span className="muted">accuracy</span>{summary.isBest && <span className="badge best">★ New best</span>}</div>
      <div className="stats">
        <div><span className="muted">Good</span><b className="score">{summary.good}</b></div>
        <div><span className="muted">Bad</span><b className="score">{summary.bad}</b></div>
        <div><span className="muted">Skipped</span><b className="score">{summary.skipped}</b></div>
      </div>
      {summary.best > 0 && !summary.isBest && <p className="muted">Best at this limit: {summary.best} good</p>}
      {saveErr && <p className="err">Could not save this session: {saveErr}</p>}
    </div>
    <div className="sc-row" style={{ justifyContent: "flex-start" }}>
      <button className="btn" onClick={start}>Play again</button>
      <button className="btn ghost" onClick={() => setScreen("menu")}>← Menu</button>
    </div>
  </div>;

  const expected = current ? norm(current.transliteration) : "";
  return <div className="sc" style={style}>
    <div className="sc-top">
      <button className="btn ghost" onClick={leave}>← Menu</button>
      {limit > 0 && status === "ready" && <span className={`sc-timer ${left <= 10 ? "low" : ""}`} role="timer">{time(left)}</span>}
      <span className="muted" aria-live="polite">{stats.total ? `${stats.ok} of ${stats.total} correct` : ""}</span>
    </div>
    {status === "loading" && <div className="sc-loading" role="status" aria-label="Loading places">
      <div className="sc-flags">{lang.countries.map((c, i) =>
        <span key={c} className={i < progress.index ? "done" : c === progress.code ? "cur" : ""}><Flag code={c} width={32} /></span>)}</div>
      <div className="bar"><i style={{ width: `${((progress.index + 0.5) / progress.total) * 100}%` }} /></div>
    </div>}
    {status === "error" && <p className="err">{msg}</p>}
    {status === "ready" && current && <div className="card">
      <div className="sc-word" lang={lang.id} dir={lang.dir}>{current.name}</div>
      {/* readOnly (not disabled) after a miss: a disabled input drops focus, so Enter would stop working. */}
      <input ref={inputRef} className={`sc-answer ${result || ""}`} value={answer}
        readOnly={result === "bad"} autoComplete="off" autoCapitalize="off" spellCheck={false}
        placeholder="Type the transliteration" aria-label="Transliteration"
        onChange={(e) => setAnswer(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); check(); } }} />
      <div className="sc-feedback" aria-live="polite">
        {result && <span className="sc-where" title={current.countryCode}><Flag code={current.countryCode} width={28} /></span>}
        {result === "ok" && <span className="sc-ok">✓ Correct</span>}
        {result === "bad" && diff && <>
          <div className="sc-diff">
            <div><span className="muted">You</span><span className="sc-mono"><Marked chars={diff[0]} /></span></div>
            <div><span className="muted">Answer</span><span className="sc-mono"><Marked chars={diff[1]} /></span></div>
          </div>
          {current.transliteration !== expected && <p className="muted sc-raw">{current.transliteration}</p>}
        </>}
      </div>
      <div className="sc-row">
        <button className="btn" onClick={check}>{result === "bad" ? "Next" : "Check"}</button>
        <button className="btn ghost" onClick={skip}>Skip</button>
      </div>
      <p className="muted sc-keys"><span><kbd>Enter</kbd> check</span><span><kbd>Esc</kbd> menu</span></p>
    </div>}
    {confirmLeave && <ConfirmDialog title="Leave this session?" body="Timed sessions are only saved when the clock runs out."
      confirmLabel="Leave" cancelLabel="Keep playing" danger
      onConfirm={() => { setConfirmLeave(false); setScreen("menu"); }}
      onCancel={() => { setConfirmLeave(false); setTimeout(() => inputRef.current?.focus(), 0); }} />}
  </div>;
}
