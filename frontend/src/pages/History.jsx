import { Fragment, useEffect, useRef, useState } from "react";
import SyncButton from "../components/SyncButton.jsx";
import { api } from "../lib/api.js";
import { MATCH_TYPE_LABELS, MOVE_TYPE_LABELS, areaHue, areaLabel, num, time, tlKey, tlLabel, tone } from "../lib/format.js";
import "./history.css";

const PAGE_SIZES = [10, 20, 50, 100];
const MAX_ROUND_COLS = 5;
const INITIAL = {
  matchType: "", moveType: "", timeLimit: "any", minScore: "", maxScore: "",
  sortBy: "date", sortDir: "desc", page: 1, pageSize: 20,
};

const Flag = ({ code }) => {
  if (!code) return null;
  const c = code.trim().toLowerCase();
  return <img className="flag" alt={code} width="24" height="18" loading="lazy"
    src={`https://flagcdn.com/w40/${c}.png`} srcSet={`https://flagcdn.com/w80/${c}.png 2x`}
    onError={(e) => { e.currentTarget.style.visibility = "hidden"; }} />;
};

// Every level that is available, most specific first: city, subregion, region, country.
const placeName = (p) => [p.city, p.subregion, p.state, p.country].filter(Boolean).join(", ");

const gameName = (g) =>
  g.match_type === "daily" ? "Daily Challenge"
  : g.match_type === "duel" ? `${MOVE_TYPE_LABELS[g.move_type] || g.move_type} Duel`
  : "Challenge";
// Only challenges get the extra line.
const modeSub = (g) => g.match_type !== "challenge" ? "" :
  [MOVE_TYPE_LABELS[g.move_type] || g.move_type, g.time_limit_sec != null ? `${Math.round(g.time_limit_sec / 60)} min` : null].filter(Boolean).join(" · ");
const fmtDate = (iso) => iso ? new Date(iso).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "—";

function SortHeader({ col, label, q, onSort, className }) {
  const active = q.sortBy === col;
  return <th className={className} aria-sort={active ? (q.sortDir === "asc" ? "ascending" : "descending") : "none"}>
    {label}
    <button type="button" className={`sort ${active ? "on" : ""}`} onClick={() => onSort(col)}
      aria-label={`Sort by ${label}${active ? (q.sortDir === "asc" ? ", currently ascending" : ", currently descending") : ""}`}>
      {active ? (q.sortDir === "asc" ? "↑" : "↓") : "↕"}
    </button>
  </th>;
}

function RoundCell({ r }) {
  if (!r) return <td />;
  return <td><div className="rc">
    <div className="rc-top"><Flag code={r.country_code} /><span style={{ color: tone(r.score) }}>{num(r.score)}</span></div>
    <span className="muted">{time(r.time_sec)}</span>
    <span className="muted">{r.steps} steps</span>
  </div></td>;
}

function PlaceLine({ kind, p }) {
  const guess = kind === "guess";
  const name = placeName(p);
  const fallback = guess && !p.has_guess ? "No guess" : "Unknown location";
  return <div className={`pl ${kind}`}>
    <span className="pl-tag">{guess ? "Your guess" : "Actual"}</span>
    <Flag code={p.country_code} />
    <span className={`pl-name ${name ? "" : "dim"}`}>{name || fallback}</span>
    {p.area_type && <span className="chip" style={{ "--h": areaHue(p.area_type) }}>{areaLabel(p.area_type)}</span>}
  </div>;
}

function Detail({ g }) {
  return <div className="rounds">{g.rounds.map((r) => <div className="rd" key={r.round_number}>
    <span className="rd-n">{r.round_number}</span>
    <div className="rd-places">
      <PlaceLine kind="real" p={{ country_code: r.country_code, country: r.country, state: r.state, subregion: r.subregion, city: r.city, area_type: r.area_type }} />
      <PlaceLine kind="guess" p={{ has_guess: r.has_guess, country_code: r.guess_country_code, country: r.guess_country, state: r.guess_state, subregion: r.guess_subregion, city: r.guess_city, area_type: r.guess_area_type }} />
    </div>
    <div className="rd-stat"><span className="muted">Time</span>{time(r.time_sec)}</div>
    <div className="rd-stat"><span className="muted">Steps</span>{r.steps}</div>
    <div>
      <div className="rd-score" style={{ color: tone(r.score) }}>{num(r.score)} <small>pts</small></div>
      <div className="rd-track"><i style={{ width: `${Math.min(100, r.score / 50)}%`, background: tone(r.score) }} /></div>
    </div>
  </div>)}</div>;
}

export default function History() {
  const [opts, setOpts] = useState(null);
  const [q, setQ] = useState(INITIAL);
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(null);
  const [err, setErr] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const appliedQ = useRef(null);

  const update = (patch) => setQ((p) => ({ ...p, page: 1, ...patch }));
  const onSort = (col) => q.sortBy === col
    ? update({ sortDir: q.sortDir === "asc" ? "desc" : "asc" })
    : update({ sortBy: col, sortDir: col === "mode" ? "asc" : "desc" });

  useEffect(() => { api("/analysis/filters").then(setOpts).catch((e) => setErr(e.message)); }, []);

  const ready = !!opts;
  // Re-runs on filter/sort/page changes AND on refreshKey (new synced games). Both go
  // through the same query, so fresh rows land wherever the current order puts them.
  useEffect(() => {
    if (!ready) return;
    let stale = false;
    const qChanged = appliedQ.current !== q;
    if (qChanged) setBusy(true);
    const t = setTimeout(() => {
      const qs = new URLSearchParams({
        time_limit: q.timeLimit, sort_by: q.sortBy, sort_dir: q.sortDir, page: q.page, page_size: q.pageSize,
      });
      if (q.matchType) qs.set("match_type", q.matchType);
      if (q.moveType) qs.set("move_type", q.moveType);
      if (q.minScore !== "") qs.set("min_score", q.minScore);
      if (q.maxScore !== "") qs.set("max_score", q.maxScore);
      api(`/history?${qs}`).then((r) => {
        if (stale) return;
        if (!r.items.length && q.page > 1) { setQ((p) => ({ ...p, page: p.page - 1 })); return; }
        appliedQ.current = q;
        setData(r); setErr(""); setBusy(false);
        if (qChanged) setOpen(null); // keep an expanded game open across sync refreshes
      }).catch((e) => { if (!stale) { setData({ items: [], total: 0 }); setErr(e.message); setBusy(false); } });
    }, 250);
    return () => { stale = true; clearTimeout(t); };
  }, [ready, q, refreshKey]);

  const refresh = () => setRefreshKey((k) => k + 1);
  const onSyncFinished = () => {
    api("/analysis/filters").then(setOpts).catch(() => {}); // new combos may exist now
    refresh();
  };

  if (!opts) return <><h2>History</h2>{err ? <p className="err">{err}</p> : <p className="muted">Loading…</p>}</>;

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / q.pageSize));
  const roundCols = MAX_ROUND_COLS;
  const colSpan = 1 + 2 + roundCols + 3;
  const from = total ? (q.page - 1) * q.pageSize + 1 : 0;
  const to = Math.min(total, q.page * q.pageSize);

  return <>
    <div className="hist-head">
      <h2>History</h2>
      <SyncButton onGameSaved={refresh} onFinished={onSyncFinished} />
    </div>
    <div className="hist-filters">
      <div><label htmlFor="h-match">Game type</label><select id="h-match" value={q.matchType} onChange={(e) => update({ matchType: e.target.value })}>
        <option value="">All</option>
        {opts.match_types.map((m) => <option key={m} value={m}>{MATCH_TYPE_LABELS[m] || m}</option>)}
      </select></div>
      <div><label htmlFor="h-move">Game mode</label><select id="h-move" value={q.moveType} onChange={(e) => update({ moveType: e.target.value })}>
        <option value="">All</option>
        {opts.move_types.map((m) => <option key={m} value={m}>{MOVE_TYPE_LABELS[m] || m}</option>)}
      </select></div>
      <div><label htmlFor="h-time">Time limit</label><select id="h-time" value={q.timeLimit} onChange={(e) => update({ timeLimit: e.target.value })}>
        <option value="any">All</option>
        {opts.time_limits.map((t) => <option key={tlKey(t)} value={tlKey(t)}>{tlLabel(t)}</option>)}
      </select></div>
      <div><label htmlFor="h-min">Min score</label><input id="h-min" type="number" value={q.minScore} onChange={(e) => update({ minScore: e.target.value })} style={{ width: 100 }} /></div>
      <div><label htmlFor="h-max">Max score</label><input id="h-max" type="number" value={q.maxScore} onChange={(e) => update({ maxScore: e.target.value })} style={{ width: 100 }} /></div>
    </div>
    {err && <p className="err">{err}</p>}
    {!data ? <p className="muted">Loading…</p> : !items.length ? <p className="muted">No games match these filters. Use “Sync games” to import your latest games.</p> :
      <div className={`tbl-wrap ${busy ? "busy" : ""}`}><table className="tbl">
        <thead><tr>
          <th aria-label="Expand" />
          <SortHeader col="date" label="Date" q={q} onSort={onSort} />
          <SortHeader col="mode" label="Game Mode" q={q} onSort={onSort} />
          {Array.from({ length: roundCols }, (_, i) => <th key={i}>Round {i + 1}</th>)}
          <SortHeader col="total_score" label="Total Score" q={q} onSort={onSort} className="num" />
          <SortHeader col="total_steps" label="Total Steps" q={q} onSort={onSort} className="num" />
          <SortHeader col="total_time" label="Total Time" q={q} onSort={onSort} className="num" />
        </tr></thead>
        <tbody>{items.map((g) => {
          const key = `${g.challenge_token}-${g.game_id}`;
          const isOpen = open === key;
          const toggle = () => setOpen(isOpen ? null : key);
          const sub = modeSub(g);
          return <Fragment key={key}>
            <tr className={`g ${isOpen ? "open" : ""}`} tabIndex={0} aria-expanded={isOpen} onClick={toggle}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } }}>
              <td className="chev-cell"><span className="chev" aria-hidden="true">▸</span></td>
              <td><b>{fmtDate(g.played_at)}</b></td>
              <td><b>{gameName(g)}</b>{sub && <div className="muted">{sub}</div>}{g.rounds.length > MAX_ROUND_COLS && <div className="more">+{g.rounds.length - MAX_ROUND_COLS} more rounds</div>}</td>
              {Array.from({ length: roundCols }, (_, i) => <RoundCell key={i} r={g.rounds[i]} />)}
              <td className="num"><span className="tot" style={{ color: tone(g.total_score / (g.rounds.length || 1)) }}>{num(g.total_score)}</span></td>
              <td className="num">{num(g.total_steps)}</td>
              <td className="num">{time(g.total_time_sec)}</td>
            </tr>
            {isOpen && <tr className="hist-detail"><td colSpan={colSpan}><Detail g={g} /></td></tr>}
          </Fragment>;
        })}</tbody>
      </table></div>}
    {items.some((g) => g.rounds.length > MAX_ROUND_COLS) && <p className="muted">Games with more than {MAX_ROUND_COLS} rounds show only the first {MAX_ROUND_COLS} here. Expand a game to see every round.</p>}
    {!!total && <div className="pager">
      <span className="muted">Showing {from}–{to} of {num(total)} games</span>
      <div className="pager-nav">
        <button className="btn ghost" disabled={q.page <= 1} onClick={() => setQ((p) => ({ ...p, page: p.page - 1 }))}>Previous</button>
        <span>Page {q.page} of {pages}</span>
        <button className="btn ghost" disabled={q.page >= pages} onClick={() => setQ((p) => ({ ...p, page: p.page + 1 }))}>Next</button>
      </div>
      <div className="pager-size"><label htmlFor="h-size" style={{ margin: 0, fontWeight: 400 }} className="muted">Per page</label>
        <select id="h-size" value={q.pageSize} onChange={(e) => update({ pageSize: Number(e.target.value) })}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
        </select></div>
    </div>}
  </>;
}
