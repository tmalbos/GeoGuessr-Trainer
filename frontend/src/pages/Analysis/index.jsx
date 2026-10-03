import { useEffect, useMemo, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";
import FilterBar from "../../components/FilterBar.jsx";
import { SkeletonCard, SkeletonRows } from "../../components/Skeleton.jsx";
import { api, errorText } from "../../lib/api.js";
import { useAppFilters } from "../../lib/filters.js";
import { navigate } from "../../lib/router.js";
import { useSync } from "../../lib/sync.jsx";
import "./analysis.css";
import Zone from "./Zone.jsx";

const SORTS = {
  weak: ["Weakest first", (a, b) => (a.score_high ?? 9999) - (b.score_high ?? 9999)],
  strong: ["Strongest first", (a, b) => (b.score_high ?? -1) - (a.score_high ?? -1)],
  rounds: ["Most rounds", (a, b) => b.total - a.total],
  name: ["A–Z", (a, b) => a.name.localeCompare(b.name)],
};

export default function Analysis({ seg, query }) {
  const { f, options, error: optError, reload: reloadOptions, ready } = useAppFilters(true);
  const { start } = useSync();
  const [levels, setLevels] = useState(null);
  const [data, setData] = useState(null);
  const [err, setErr] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [search, setSearch] = useState(query.get("q") ?? "");
  const [sort, setSort] = useState("weak");
  const [open, setOpen] = useState(() => new Set());

  const tiers = options?.tiers ?? [];
  const usable = ready && !!f.matchType && !!f.moveType && f.timeLimit !== "any";
  const qs = `match_type=${f.matchType}&move_type=${f.moveType}&time_limit=${f.timeLimit}`;

  useEffect(() => {
    if (!usable) return undefined;
    let off = false;
    setLevels(null); setData(null); setErr("");
    api(`/analysis/levels?${qs}`).then((l) => !off && setLevels(l)).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [usable, qs, attempt]);

  const level = levels?.find((l) => l.level === seg[0])?.level ?? levels?.[0]?.level;

  useEffect(() => {
    if (!level) return undefined;
    let off = false;
    setData(null);
    api(`/analysis/${level}?${qs}`).then((d) => !off && setData(d)).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [level, qs, attempt]);

  // Few zones: show everything open. Many: start collapsed, except the one a link pointed at (?q=Spain).
  useEffect(() => {
    if (!data) return;
    const q = (query.get("q") ?? "").toLowerCase();
    setOpen(new Set(data.zones.filter((z) => data.zones.length <= 3 || (q && z.name.toLowerCase() === q)).map((z) => z.name)));
  }, [data]); // eslint-disable-line react-hooks/exhaustive-deps

  const shown = useMemo(() => {
    const s = search.trim().toLowerCase();
    const zones = (data?.zones ?? []).filter((z) => !s || z.name.toLowerCase().includes(s));
    return [...zones].sort(SORTS[sort][1]);
  }, [data, search, sort]);

  const toggle = (name) => setOpen((o) => { const n = new Set(o); if (!n.delete(name)) n.add(name); return n; });
  const allOpen = shown.length > 0 && shown.every((z) => open.has(z.name));
  const retry = () => { setErr(""); setAttempt((a) => a + 1); };

  const head = <h2>Analysis</h2>;
  if (optError) return <>{head}<ErrorBox message={optError} onRetry={reloadOptions} /></>;
  if (!ready) return <>{head}<SkeletonRows n={3} h={60} /></>;
  if (!options.combos.length) return <>{head}<div className="empty"><p className="muted">No games yet.</p><button type="button" className="btn" onClick={start}>Sync games</button></div></>;

  return <>{head}
    <FilterBar f={f} options={options} exact />
    {err ? <ErrorBox message={err} onRetry={retry} /> :
     !levels ? <SkeletonRows n={2} h={34} /> :
     !levels.length ? <p className="muted">No games for this combination yet.</p> : <>
       <div className="tabs">{levels.map((l) =>
         <button key={l.level} className={l.level === level ? "on" : ""} onClick={() => navigate(`/analysis/${l.level}`)}
           title={l.level === "general" ? undefined : `${l.zones} zone${l.zones === 1 ? "" : "s"} with 10+ rounds`}>
           {l.label}{l.level !== "general" && <span className="tab-n">{l.zones}</span>}</button>)}</div>
       {!data ? <><SkeletonCard /><SkeletonCard /></> : !data.zones.length ? <p className="muted">No zones with 10+ rounds at this level.</p> : <>
         {data.zones.length > 3 && <div className="zone-tools">
           <input type="search" aria-label="Search zones" placeholder="Search" value={search} onChange={(e) => setSearch(e.target.value)} />
           <select aria-label="Sort zones" value={sort} onChange={(e) => setSort(e.target.value)}>
             {Object.entries(SORTS).map(([k, [label]]) => <option key={k} value={k}>{label}</option>)}</select>
           <button type="button" className="btn ghost" onClick={() => setOpen(allOpen ? new Set() : new Set(shown.map((z) => z.name)))}>{allOpen ? "Collapse all" : "Expand all"}</button>
         </div>}
         {shown.length ? shown.map((z) => <Zone key={z.name} z={z} tiers={tiers} open={open.has(z.name)} onToggle={() => toggle(z.name)} />)
           : <p className="muted">No zone matches “{search}”.</p>}
       </>}
     </>}
  </>;
}
