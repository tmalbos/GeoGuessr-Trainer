import { Fragment, useEffect, useRef, useState } from "react";
import ErrorBox from "../components/ErrorBox.jsx";
import FilterBar from "../components/FilterBar.jsx";
import Flag from "../components/Flag.jsx";
import RangeSlider from "../components/RangeSlider.jsx";
import { SkeletonRows } from "../components/Skeleton.jsx";
import SyncButton from "../components/SyncButton.jsx";
import { api, errorText } from "../lib/api.js";
import { MAX_TOTAL, resetFilters, setFilters, useAppFilters } from "../lib/filters.js";
import { MOVE_TYPE_LABELS, areaHue, areaLabel, num, time, tone } from "../lib/format.js";
import { useSync, useSyncEvents } from "../lib/sync.jsx";
import ReplayReport from "./ReplayReport/index.jsx";
import "./history.css";

const PAGE_SIZES = [10, 20, 50, 100];
const MAX_ROUND_COLS = 5;

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
const fmtK = (v, isMax) => (isMax && v >= MAX_TOTAL ? `${MAX_TOTAL / 1000}k+` : v >= 1000 ? `${+(v / 1000).toFixed(1)}k` : String(v));

const svgProps = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const CopyIcon = () => <svg {...svgProps}><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V6a2 2 0 0 1 2-2h9" /></svg>;
const CheckIcon = () => <svg {...svgProps}><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>;
const PinIcon = () => <svg {...svgProps}><path d="M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11z" /><circle cx="12" cy="10" r="2.5" /></svg>;

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch {
    // Fallback for contexts where the Clipboard API is unavailable.
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
  }
}

function CoordButtons({ lat, lng }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef(null);
  useEffect(() => () => clearTimeout(timer.current), []);
  if (lat == null || lng == null) return null;
  const text = `${lat},${lng}`;
  const onCopy = async () => {
    await copyText(text);
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 1200);
  };
  return <span className="pl-actions">
    <button type="button" className={`pl-btn ${copied ? "done" : ""}`} onClick={onCopy}
      title={copied ? "Copied" : "Copy coordinates"} aria-label="Copy coordinates">
      {copied ? <CheckIcon /> : <CopyIcon />}
    </button>
    <a className="pl-btn" href={`https://www.google.com/maps/@?api=1&map_action=pano&viewpoint=${lat},${lng}`}
      target="_blank" rel="noopener noreferrer" title="Open in Google Maps" aria-label="Open in Google Maps">
      <PinIcon />
    </a>
  </span>;
}

function SortHeader({ col, label, sort, onSort, className }) {
  const active = sort.by === col;
  return <th className={className} aria-sort={active ? (sort.dir === "asc" ? "ascending" : "descending") : "none"}>
    <button type="button" className={`sort-head ${active ? "on" : ""}`} onClick={() => onSort(col)}>
      {label}<span className="sort-ico" aria-hidden="true">{active ? (sort.dir === "asc" ? "↑" : "↓") : "↕"}</span>
    </button>
  </th>;
}

function RoundCell({ r }) {
  if (!r) return <td />;
  return <td><div className="rc">
    <div className="rc-top"><Flag code={r.country_code} width={24} /><span style={{ color: tone(r.score) }}>{num(r.score)}</span></div>
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
    <Flag code={p.country_code} width={24} />
    <span className={`pl-name ${name ? "" : "dim"}`}>{name || fallback}</span>
    {p.area_type && <span className="chip" style={{ "--h": areaHue(p.area_type) }}>{areaLabel(p.area_type)}</span>}
    <CoordButtons lat={p.lat} lng={p.lng} />
  </div>;
}

function Detail({ g, onReport }) {
  return <div className="rounds">
    <div style={{ padding: "12px 0 4px" }}>
      <button type="button" className="btn ghost" onClick={() => onReport(g)}>Replay report</button>
    </div>
    {g.rounds.map((r) => <div className="rd" key={r.round_number}>
      <span className="rd-n">{r.round_number}</span>
      <div className="rd-places">
        <PlaceLine kind="real" p={{ country_code: r.country_code, country: r.country, state: r.state, subregion: r.subregion, city: r.city, area_type: r.area_type, lat: r.lat, lng: r.lng }} />
        <PlaceLine kind="guess" p={{ has_guess: r.has_guess, country_code: r.guess_country_code, country: r.guess_country, state: r.guess_state, subregion: r.guess_subregion, city: r.guess_city, area_type: r.guess_area_type, lat: r.guess_lat, lng: r.guess_lng }} />
      </div>
      <div className="rd-stat"><span className="muted">Time</span>{time(r.time_sec)}</div>
      <div className="rd-stat"><span className="muted">Steps</span>{r.steps}</div>
      <div>
        <div className="rd-score" style={{ color: tone(r.score) }}>{num(r.score)} <small>pts</small></div>
        <div className="rd-track"><i style={{ width: `${Math.min(100, r.score / 50)}%`, background: tone(r.score) }} /></div>
      </div>
    </div>)}
  </div>;
}

export default function History() {
  const { f, options, error: optError, reload: reloadOptions, ready } = useAppFilters(false);
  const { start } = useSync();
  const [sort, setSort] = useState({ by: "date", dir: "desc" });
  const [pageSize, setPageSize] = useState(20);
  const [pg, setPg] = useState({ n: 1, key: "" });
  const [data, setData] = useState(null);
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(null);
  const [err, setErr] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [reportGame, setReportGame] = useState(null);
  const appliedKey = useRef(null);

  // The page resets to 1 whenever a filter, the sort or the page size changes.
  const queryKey = [f.matchType, f.moveType, f.timeLimit, f.minScore, f.maxScore, sort.by, sort.dir, pageSize].join("|");
  const page = pg.key === queryKey ? pg.n : 1;
  const setPage = (n) => setPg({ n, key: queryKey });
  const refresh = () => setRefreshKey((k) => k + 1);
  useSyncEvents({ onGameSaved: refresh, onFinished: refresh });

  const onSort = (col) => setSort((s) => s.by === col
    ? { by: col, dir: s.dir === "asc" ? "desc" : "asc" }
    : { by: col, dir: col === "mode" ? "asc" : "desc" });

  const active = !!(f.matchType || f.moveType || f.timeLimit !== "any" || f.minScore > 0 || f.maxScore < MAX_TOTAL);

  // Re-runs on filter/sort/page changes AND on refreshKey (new synced games).
  useEffect(() => {
    if (!ready) return undefined;
    let stale = false;
    setBusy(true);
    const t = setTimeout(() => {
      const qs = new URLSearchParams({ time_limit: f.timeLimit, sort_by: sort.by, sort_dir: sort.dir, page, page_size: pageSize });
      if (f.matchType) qs.set("match_type", f.matchType);
      if (f.moveType) qs.set("move_type", f.moveType);
      if (f.minScore > 0) qs.set("min_score", f.minScore);
      if (f.maxScore < MAX_TOTAL) qs.set("max_score", f.maxScore);
      api(`/history?${qs}`).then((r) => {
        if (stale) return;
        if (!r.items.length && page > 1) { setPage(page - 1); return; }
        setData(r); setErr(""); setBusy(false);
        if (appliedKey.current !== queryKey) setOpen(null); // keep an expanded game open across sync refreshes
        appliedKey.current = queryKey;
      }).catch((e) => { if (!stale) { setData({ items: [], total: 0 }); setErr(errorText(e)); setBusy(false); } });
    }, 250);
    return () => { stale = true; clearTimeout(t); };
  }, [ready, queryKey, page, refreshKey]); // eslint-disable-line react-hooks/exhaustive-deps

  const head = <div className="hist-head"><h2>History</h2><SyncButton /></div>;
  if (optError) return <>{head}<ErrorBox message={optError} onRetry={reloadOptions} /></>;
  if (!ready) return <>{head}<SkeletonRows n={6} /></>;

  // The hooks above stay mounted, so filters, page and the expanded game survive coming back.
  if (reportGame) return <ReplayReport g={reportGame} title={`${gameName(reportGame)} · ${fmtDate(reportGame.played_at)}`} onBack={() => setReportGame(null)} />;

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const colSpan = 1 + 2 + MAX_ROUND_COLS + 3;
  const from = total ? (page - 1) * pageSize + 1 : 0;
  const to = Math.min(total, page * pageSize);

  return <>
    {head}
    <FilterBar f={f} options={options}>
      <div><label>Total score</label>
        <RangeSlider label="Total score" min={0} max={MAX_TOTAL} step={500} value={[f.minScore, f.maxScore]} format={fmtK}
          onChange={([a, b]) => setFilters({ minScore: a, maxScore: b })} /></div>
      {active && <button type="button" className="btn ghost filter-clear" onClick={resetFilters}>Clear filters</button>}
    </FilterBar>
    {err && <ErrorBox message={err} onRetry={refresh} />}
    {!data && !err ? <SkeletonRows n={6} /> : !items.length && !err ?
      <div className="empty">
        <p className="muted">{active ? "No games match these filters." : "No games yet."}</p>
        {active ? <button type="button" className="btn" onClick={resetFilters}>Clear filters</button>
          : <button type="button" className="btn" onClick={start}>Sync games</button>}
      </div> : items.length > 0 &&
      <div className={`tbl-wrap ${busy ? "busy" : ""}`}><table className="tbl">
        <thead><tr>
          <th aria-label="Expand" />
          <SortHeader col="date" label="Date" sort={sort} onSort={onSort} />
          <SortHeader col="mode" label="Game Mode" sort={sort} onSort={onSort} />
          {Array.from({ length: MAX_ROUND_COLS }, (_, i) => <th key={i}>Round {i + 1}</th>)}
          <SortHeader col="total_score" label="Total Score" sort={sort} onSort={onSort} className="num" />
          <SortHeader col="total_steps" label="Total Steps" sort={sort} onSort={onSort} className="num" />
          <SortHeader col="total_time" label="Total Time" sort={sort} onSort={onSort} className="num" />
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
              {Array.from({ length: MAX_ROUND_COLS }, (_, i) => <RoundCell key={i} r={g.rounds[i]} />)}
              <td className="num"><span className="tot" style={{ color: tone(g.total_score / (g.rounds.length || 1)) }}>{num(g.total_score)}</span></td>
              <td className="num">{num(g.total_steps)}</td>
              <td className="num">{time(g.total_time_sec)}</td>
            </tr>
            {isOpen && <tr className="hist-detail"><td colSpan={colSpan}><Detail g={g} onReport={setReportGame} /></td></tr>}
          </Fragment>;
        })}</tbody>
      </table></div>}
    {items.some((g) => g.rounds.length > MAX_ROUND_COLS) && <p className="muted">Games with more than {MAX_ROUND_COLS} rounds show only the first {MAX_ROUND_COLS} here. Expand a game to see every round.</p>}
    {!!total && <div className="pager">
      <span className="muted">Showing {from}–{to} of {num(total)} games</span>
      <div className="pager-nav">
        <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Previous</button>
        <span>Page {page} of {pages}</span>
        <button className="btn ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next</button>
      </div>
      <div className="pager-size"><label htmlFor="h-size" style={{ margin: 0, fontWeight: 400 }} className="muted">Per page</label>
        <select id="h-size" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))}>
          {PAGE_SIZES.map((n) => <option key={n} value={n}>{n}</option>)}
        </select></div>
    </div>}
  </>;
}
