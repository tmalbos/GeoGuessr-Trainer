import { useCallback, useEffect, useRef, useState } from "react";
import ErrorBox from "../../../components/ErrorBox.jsx";
import { SkeletonCard } from "../../../components/Skeleton.jsx";
import { useToast } from "../../../components/Toast.jsx";
import { api, errorText } from "../../../lib/api.js";
import { invalidateClueCountries, setHasClues } from "../clueCountries.js";
import ClueCard from "./ClueCard.jsx";
import ClueEditor from "./ClueEditor.jsx";
import EmptyCat from "./EmptyCat.jsx";
import { LocationIcon } from "./tagUi.jsx";
import { CATEGORY_ICONS, GENERAL_TAGS, LOCATION_TAGS, tagHue } from "./tags.js";
import "./clues.css";
import "./clues-extra.css";

const catOf = (c) => c.tags.find((t) => GENERAL_TAGS.includes(t));
const locOf = (c) => c.tags.find((t) => LOCATION_TAGS.includes(t));
const UNDO_MS = 5000;
const SHOWN = ["visible", "guide-only"]; // what counts as "has clues" on the world map

export default function ClueList({ id, name, editing }) {
  const toast = useToast();
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const [form, setForm] = useState(null); // null | "new" | a clue being edited
  const [version, setVersion] = useState(0);
  const [hidden, setHidden] = useState(() => new Set()); // deleted but still undoable
  const [cat, setCat] = useState("");
  const [loc, setLoc] = useState("");
  const pending = useRef(new Map()); // key -> { cc, clueId }, deletes waiting out their undo window

  useEffect(() => {
    let off = false;
    setItems(null); setErr("");
    api(`/clues/${id}${editing ? "?all=true" : ""}`).then((d) => {
        if (off) return;
        setItems(d);
        // This list is the truth about whether the country has clues: tell the world map without refetching.
        setHasClues(id, d.some((c) => SHOWN.includes(c.visibility ?? "visible")));
      }).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [id, editing, version]);

  useEffect(() => { setCat(""); setLoc(""); setForm(null); }, [id]);
  useEffect(() => { if (!editing) setForm(null); }, [editing]);

  const reload = () => setVersion((v) => v + 1);
  const unhide = (key) => setHidden((h) => { const n = new Set(h); n.delete(key); return n; });

  const commit = useCallback(async (key) => {
    const e = pending.current.get(key);
    if (!e) return; // undone, or already flushed
    pending.current.delete(key);
    try { await api(`/clues/${e.cc}/${e.clueId}`, { method: "DELETE" }); }
    catch (ex) { toast.push(errorText(ex), { kind: "err", ms: 6000 }); unhide(key); }
    setVersion((v) => v + 1);
  }, [toast]);

  // Leaving the page mid-undo-window: finish the deletes instead of silently dropping them.
  useEffect(() => () => {
    for (const e of pending.current.values()) api(`/clues/${e.cc}/${e.clueId}`, { method: "DELETE" }).catch(() => {}).finally(invalidateClueCountries);
    pending.current.clear();
  }, []);

  const remove = (c) => {
    const key = `${id}/${c.id}`;
    pending.current.set(key, { cc: id, clueId: c.id });
    setHidden((h) => new Set(h).add(key));
    toast.push("Clue deleted", {
      ms: UNDO_MS,
      action: { label: "Undo", onClick: () => { pending.current.delete(key); unhide(key); } },
      onExpire: () => commit(key),
    });
  };

  const visible = (items ?? []).filter((c) => !hidden.has(`${id}/${c.id}`));
  const cats = GENERAL_TAGS.filter((t) => visible.some((c) => catOf(c) === t));
  const count = (t) => visible.filter((c) => catOf(c) === t).length;
  const filtered = visible.filter((c) => (!cat || catOf(c) === cat) && (!loc || locOf(c) === loc));
  const grouped = !cat && cats.length > 1;
  const card = (c) => <ClueCard key={c.id} cc={id} clue={c} editing={editing} onDelete={remove} onEdit={setForm} />;
  const clear = () => { setCat(""); setLoc(""); };

  let list;
  if (!filtered.length) {
    list = <p className="muted">No clues match. <button type="button" className="link-btn" onClick={clear}>Clear filters</button></p>;
  } else if (grouped) {
    const other = filtered.filter((c) => !catOf(c));
    list = <>
      {cats.map((t) => {
        const g = filtered.filter((c) => catOf(c) === t);
        return g.length > 0 && <section key={t}>
          <h4 className="clue-group"><span aria-hidden="true">{CATEGORY_ICONS[t]}</span> {t} <span className="count">{g.length}</span></h4>
          {g.map(card)}
        </section>;
      })}
      {other.map(card)}
    </>;
  } else {
    list = filtered.map(card);
  }

  return <>
    {editing && <div className="clue-bar">
      <button type="button" className="btn" onClick={() => setForm("new")}>+ Add clue</button>
    </div>}
    {err && <ErrorBox message={err} onRetry={() => { setErr(""); reload(); }} />}
    {!items && !err ? <><SkeletonCard /><SkeletonCard /></>
      : items && !visible.length ? (editing ? null : <EmptyCat />)
      : items && <>
        {visible.length >= 4 && <div className="clue-filter">
          <div className="clue-chips" role="group" aria-label="Filter by category">
            <button type="button" className={`fchip ${!cat ? "on" : ""}`} aria-pressed={!cat} onClick={() => setCat("")}>All</button>
            {cats.map((t) => <button key={t} type="button" className={`fchip ${cat === t ? "on" : ""}`} aria-pressed={cat === t} onClick={() => setCat(cat === t ? "" : t)}>
              <span aria-hidden="true">{CATEGORY_ICONS[t]}</span> {t}<span className="count">{count(t)}</span></button>)}
          </div>
          <div className="clue-filter-row">
            <div className="clue-chips" role="group" aria-label="Filter by location">
              {LOCATION_TAGS.map((t) => <button key={t} type="button" className={`fchip loc ${loc === t ? "on" : ""}`} style={{ "--h": tagHue(t) }}
                aria-pressed={loc === t} onClick={() => setLoc(loc === t ? "" : t)}><LocationIcon t={t} /> {t}</button>)}
            </div>
          </div>
        </div>}
        {list}
      </>}
    {form && <ClueEditor key={form === "new" ? "new" : form.id} cc={id} name={name} clue={form === "new" ? null : form} onClose={() => setForm(null)}
      onSaved={() => { toast.push("Clue saved", { kind: "ok" }); setForm(null); reload(); }} />}
  </>;
}
