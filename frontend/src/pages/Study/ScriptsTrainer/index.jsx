import { Fragment, useEffect, useRef, useState } from "react";
import { defaultNormalizeAnswer, loadPlaces } from "./geonames.js";
import LANGUAGES from "./languages/index.js";
import "./scripts.css";

const shuffle = (arr) => {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
};

export default function ScriptsTrainer({ onExit }) {
  const [screen, setScreen] = useState("lang"); // lang | menu | game | ref
  const [lang, setLang] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | loading | ready | error
  const [msg, setMsg] = useState("");
  const [current, setCurrent] = useState(null);
  const [answer, setAnswer] = useState("");
  const [result, setResult] = useState(null); // null | ok | bad
  const [stats, setStats] = useState({ ok: 0, total: 0 });
  const cache = useRef({});
  const queue = useRef([]);
  const timer = useRef(null);
  const inputRef = useRef(null);
  const extraRef = useRef(null);

  const nextPlace = () => {
    clearTimeout(timer.current);
    if (!queue.current.length) queue.current = shuffle([...cache.current[lang.id]]);
    setCurrent(queue.current.pop());
    setAnswer("");
    setResult(null);
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  const start = async () => {
    setStats({ ok: 0, total: 0 });
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

  const check = () => {
    if (!current) return;
    if (result) return nextPlace();
    if (!answer.trim()) return;
    const norm = lang.normalizeAnswer || defaultNormalizeAnswer;
    const right = norm(answer) === norm(current.transliteration);
    setStats((s) => ({ ok: s.ok + (right ? 1 : 0), total: s.total + 1 }));
    setResult(right ? "ok" : "bad");
    if (right) timer.current = setTimeout(nextPlace, 450);
  };

  useEffect(() => {
    if (screen !== "game") return;
    const onKey = (e) => { if (e.key === "Escape") setScreen("menu"); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [screen]);

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
    <div className="sc-list">
      <button className="btn" onClick={start}>Start practicing</button>
      <button className="btn ghost" onClick={() => setScreen("ref")}>Reference table</button>
      <button className="btn ghost" onClick={() => setScreen("lang")}>← Change language</button>
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

  return <div className="sc" style={style}>
    <div className="sc-top">
      <button className="btn ghost" onClick={() => setScreen("menu")}>← Menu</button>
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
        <button className="btn ghost" onClick={nextPlace}>Skip</button>
      </div>
      <p className="muted" style={{ textAlign: "center" }}>Enter to check · Esc for menu</p>
    </div>}
  </div>;
}
