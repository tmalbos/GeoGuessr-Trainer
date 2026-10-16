import { useEffect, useState } from "react";
import Flag from "../../../components/Flag.jsx";
import { api, errorText } from "../../../lib/api.js";
import { useCountries } from "./countries.js";
import { REL_TYPES, sameRel } from "./relations.js";
import "./relations.css";

const trunc = (s, n) => (s.length > n ? `${s.slice(0, n)}…` : s);

/** Pick another clue to relate to. same_as / similar_to: subcontinent -> country -> clue. group: a clue of this country.
 *  `tags` = the current clue's { general, location }: other countries' clues are filtered by them
 *  (unless none match, then everything is shown; "Show all" always works). */
export default function RelatePicker({ cc, clueId, tags, taken, onPick, onClose }) {
  const { nameOf } = useCountries();
  const me = cc.toUpperCase();
  const [type, setType] = useState("same_as");
  const [subs, setSubs] = useState(null);
  const [sub, setSub] = useState("");
  const [country, setCountry] = useState("");
  const [clues, setClues] = useState(null);
  const [all, setAll] = useState(false);
  const [err, setErr] = useState("");

  // Escape closes only this picker, not the editor underneath.
  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") { e.stopPropagation(); e.preventDefault(); onClose(); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  useEffect(() => {
    let off = false;
    api("/clues/subcontinents").then((d) => {
      if (off) return;
      setSubs(d);
      setSub((d.find((s) => s.countries.includes(me)) ?? d.find((s) => s.countries.some((c) => c !== me)) ?? d[0])?.id ?? "");
    }).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [me]);

  const target = type === "group" ? me : country;
  useEffect(() => {
    setClues(null); setAll(false);
    if (!target) return undefined;
    let off = false;
    api(`/clues/${target}?all=true`).then((d) => !off && setClues(d)).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [target]);

  const pickType = (t) => { setType(t); setCountry(""); setErr(""); };
  const current = subs?.find((s) => s.id === sub);
  const countries = (current?.countries ?? []).filter((c) => c !== me);

  const pool = (clues ?? []).filter((c) => !(target === me && c.id === clueId));
  const tagged = type !== "group" && !!tags.general && !!tags.location;
  const matching = tagged ? pool.filter((c) => c.tags.includes(tags.general) && c.tags.includes(tags.location)) : pool;
  const filtering = tagged && matching.length > 0 && matching.length < pool.length;
  const shown = filtering && !all ? matching : pool;

  return <div className="rp-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="rp" role="dialog" aria-modal="true" aria-label="Relate clue">
      <h3>Relate this clue</h3>
      <div className="tabs" role="group" aria-label="Relation type">
        {REL_TYPES.map(([k, l]) => <button key={k} type="button" className={type === k ? "on" : ""} aria-pressed={type === k} onClick={() => pickType(k)}>{l}</button>)}
      </div>
      {err && <p className="err">{err}</p>}

      {type !== "group" && <>
        <h4>Subcontinent</h4>
        {!subs ? <p className="muted">Loading…</p> : <div className="tabs" role="group" aria-label="Subcontinent">
          {subs.map((s) => <button key={s.id} type="button" className={sub === s.id ? "on" : ""} aria-pressed={sub === s.id}
            onClick={() => { setSub(s.id); setCountry(""); }}>{s.label}</button>)}
        </div>}
        <h4>Country</h4>
        {subs && !countries.length ? <p className="muted">No other country in this subcontinent has clues yet.</p>
          : <div className="rp-countries">
              {countries.map((c) => <button key={c} type="button" className={`fchip ${country === c ? "on" : ""}`} aria-pressed={country === c}
                onClick={() => setCountry(c)}><Flag code={c} width={18} /> {nameOf(c)}</button>)}
            </div>}
      </>}

      {target && <>
        <h4>{type === "group" ? "Clues of this country" : `Clues of ${nameOf(target)}`}</h4>
        {!clues ? <p className="muted">Loading…</p> : !shown.length ? <p className="muted">No clues here.</p> : <>
          {filtering && <p className="muted">
            {all ? "Showing every clue. " : `Showing ${matching.length} of ${pool.length}, matching ${tags.general} · ${tags.location}. `}
            <button type="button" className="link-btn" onClick={() => setAll((a) => !a)}>{all ? "Only matching tags" : "Show all"}</button>
          </p>}
          <div className="rp-list">{shown.map((c) => {
            const rel = { type, cc: target, id: c.id };
            const dup = taken.some((t) => sameRel(t, rel));
            return <button key={c.id} type="button" className="rp-clue" disabled={dup} onClick={() => onPick(rel)}>
              {c.image ? <img src={`/api/clues/${target}/files/${c.image}`} alt="" loading="lazy" /> : <span className="rp-noimg">Map</span>}
              <span className="rp-info">
                <span className="rp-tags">{c.tags.join(" · ")}{dup ? " · already related" : ""}</span>
                {trunc(c.info, 140)}
              </span>
            </button>;
          })}</div>
        </>}
      </>}

      <div className="rp-actions"><button type="button" className="btn ghost" onClick={onClose}>Cancel</button></div>
    </div>
  </div>;
}
