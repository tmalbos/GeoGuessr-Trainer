import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ErrorBox from "../../../components/ErrorBox.jsx";
import { SkeletonCard } from "../../../components/Skeleton.jsx";
import { useToast } from "../../../components/Toast.jsx";
import { api, errorText } from "../../../lib/api.js";
import { invalidateClueCountries, setHasClues } from "../clueCountries.js";
import ClueCard, { SourceChip } from "./ClueCard.jsx";
import ClueEditor from "./ClueEditor.jsx";
import EmptyCat from "./EmptyCat.jsx";
import { groupBlocks } from "./relations.js";
import { LocationIcon } from "./tagUi.jsx";
import { CATEGORY_ICONS, GENERAL_TAGS, LOCATION_TAGS, tagHue } from "./tags.js";
import "./clues.css";
import "./clues-extra.css";
import "./relations.css";

const catOf = (c) => c.tags.find((t) => GENERAL_TAGS.includes(t));
const locOf = (c) => c.tags.find((t) => LOCATION_TAGS.includes(t));
const UNDO_MS = 5000;
const SHOWN = ["visible", "guide-only"]; // what counts as "has clues" on the world map
const Funnel = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 4h18l-7 8v6l-4 2v-8z" /></svg>;

/** The clue filters, drawn into the page's side panel (`slotEl`) instead of above the list.
 *  Collapsed behind a toggle by default; shows the chips only while expanded. */
function FilterPanel({ slotEl, cat, loc, cats, count, onCat, onLoc }) {
  const [open, setOpen] = useState(false);
  const active = (cat ? 1 : 0) + (loc ? 1 : 0);
  return createPortal(<>
    <button type="button" className="cf-toggle" aria-expanded={open}
      aria-label="Filter the clues" title="Filter the clues" onClick={() => setOpen((o) => !o)}>
      <Funnel />
      {active > 0 && <span className="count">{active}</span>}
    </button>
    {open && <div className="clue-filter">
      <div className="clue-chips" role="group" aria-label="Filter by category">
        <button type="button" className={`fchip ${!cat ? "on" : ""}`} aria-pressed={!cat} onClick={() => onCat("")}>All</button>
        {cats.map((t) => <button key={t} type="button" className={`fchip ${cat === t ? "on" : ""}`} aria-pressed={cat === t} onClick={() => onCat(cat === t ? "" : t)}>
          <span aria-hidden="true">{CATEGORY_ICONS[t]}</span> {t}<span className="count">{count(t)}</span></button>)}
      </div>
      <div className="clue-filter-row">
        <div className="clue-chips" role="group" aria-label="Filter by location">
          {LOCATION_TAGS.map((t) => <button key={t} type="button" className={`fchip loc ${loc === t ? "on" : ""}`} style={{ "--h": tagHue(t) }}
            aria-pressed={loc === t} onClick={() => onLoc(loc === t ? "" : t)}><LocationIcon t={t} /> {t}</button>)}
        </div>
      </div>
    </div>}
  </>, slotEl);
}

export default function ClueList({ id, name, editing }) {
  const toast = useToast();
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const [form, setForm] = useState(null); // null | "new" | a clue being edited
  const [version, setVersion] = useState(0);
  const [hidden, setHidden] = useState(() => new Set()); // deleted but still undoable
  const [cat, setCat] = useState("");
  const [loc, setLoc] = useState("");
  const [drag, setDrag] = useState(null); // { id, key } of the clue being dragged
  const [over, setOver] = useState(null); // id of the clue it is hovering
  const pending = useRef(new Map()); // key -> { cc, clueId }, deletes waiting out their undo window
  const [fslot, setFslot] = useState(null); // the side panel element the filters render into

  useEffect(() => { setFslot(document.getElementById("clue-filter-slot")); }, []);

  useEffect(() => {
    let off = false;
    // The old list stays on screen while this runs: blanking it first shrank the column and yanked
    // the scroll position back to the top every time a clue was saved or deleted.
    api(`/clues/${id}${editing ? "?all=true" : ""}`).then((d) => {
        if (off) return;
        setItems(d);
        setErr("");
        // This list is the truth about whether the country has clues: tell the world map without refetching.
        setHasClues(id, d.some((c) => SHOWN.includes(c.visibility ?? "visible")));
      }).catch((e) => { if (!off) { setErr(errorText(e)); setItems(null); } });
    return () => { off = true; };
  }, [id, editing, version]);

  useEffect(() => { setItems(null); setErr(""); setCat(""); setLoc(""); setForm(null); }, [id]);
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

  // Drag a clue by its grip onto another one in the same block: it takes that clue's place.
  // The saved order is the country's full list, so only the dragged clue moves in it.
  const moveTo = async (fromId, toId) => {
    if (!items) return;
    const ids = items.map((x) => x.id);
    const a = ids.indexOf(fromId);
    const b = ids.indexOf(toId);
    if (a < 0 || b < 0 || a === b) return;
    ids.splice(b, 0, ids.splice(a, 1)[0]);
    const byId = new Map(items.map((x) => [x.id, x]));
    const previous = items;
    setItems(ids.map((i) => byId.get(i)));
    try { await api(`/clues/${id}/reorder`, { method: "POST", body: { ids } }); }
    catch (ex) { setItems(previous); toast.push(errorText(ex), { kind: "err", ms: 6000 }); }
  };
  const release = () => { setDrag(null); setOver(null); };
  const grab = (e, c, key) => {
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", c.id);
    const card = e.currentTarget.closest("article");
    if (card) {
      const r = card.getBoundingClientRect();
      e.dataTransfer.setDragImage(card, e.clientX - r.left, e.clientY - r.top);
    }
    setDrag({ id: c.id, key });
  };
  const hover = (e, c, key) => {
    if (!drag || drag.key !== key || drag.id === c.id) return; // only inside the same block
    e.preventDefault();
    setOver(c.id);
  };
  const drop = (e, c, key) => {
    e.preventDefault();
    if (drag && drag.key === key && drag.id !== c.id) moveTo(drag.id, c.id);
    release();
  };

  const visible = (items ?? []).filter((c) => !hidden.has(`${id}/${c.id}`));
  const cats = GENERAL_TAGS.filter((t) => visible.some((c) => catOf(c) === t));
  const count = (t) => visible.filter((c) => catOf(c) === t).length;
  const filtered = visible.filter((c) => (!cat || catOf(c) === cat) && (!loc || locOf(c) === loc));
  // `key` = the block the card is shown in (clues can only be dragged inside their own block).
  const card = (c, key, opts = {}) => <ClueCard key={c.id} cc={id} clue={c} editing={editing} onDelete={remove} onEdit={setForm}
    dragging={drag?.id === c.id} over={over === c.id} showTags={opts.showTags !== false} showSource={opts.showSource !== false}
    onGrab={(e) => grab(e, c, key)} onOver={(e) => hover(e, c, key)} onDrop={(e) => drop(e, c, key)} onRelease={release} />;
  // Clues joined by a "group" relation are drawn together inside one box. The tags of the first clue and
  // the source(s) of the whole group are shown once, for the box, instead of repeating on every clue.
  const blocks = (arr, key) => groupBlocks(arr).map((b) => {
    if (b.length === 1) return card(b[0], key);
    const sources = [...new Set(b.map((c) => c.source).filter(Boolean))];
    return <div className="clue-box" key={`box-${b[0].id}`}>
      {b.map((c, i) => card(c, key, { showTags: i === 0, showSource: false }))}
      {sources.length > 0 && <div className="clue-box-src">
        {sources.map((s) => <SourceChip key={s} source={s} />)}
      </div>}
    </div>;
  });
  const clear = () => { setCat(""); setLoc(""); };

  let list;
  if (!filtered.length) {
    list = <p className="muted">No clues match. <button type="button" className="link-btn" onClick={clear}>Clear filters</button></p>;
  } else {
    // National clues are one plain block. Regional ones get a heading per category.
    const nat = filtered.filter((c) => c.scope !== "reg");
    const reg = filtered.filter((c) => c.scope === "reg");
    const regCats = GENERAL_TAGS.filter((t) => reg.some((c) => catOf(c) === t));
    const grouped = !cat && regCats.length > 1;
    list = <>
      {blocks(nat, "nat")}
      {grouped ? <>
        {regCats.map((t) => {
          const g = reg.filter((c) => catOf(c) === t);
          return <section key={t}>
            <h4 className="clue-group"><span aria-hidden="true">{CATEGORY_ICONS[t]}</span> {t} <span className="count">{g.length}</span></h4>
            {blocks(g, t)}
          </section>;
        })}
        {blocks(reg.filter((c) => !catOf(c)), "reg")}
      </> : blocks(reg, "reg")}
    </>;
  }

  return <>
    {editing && <div className="clue-bar">
      <button type="button" className="btn" onClick={() => setForm("new")}>+ Add clue</button>
    </div>}
    {err && <ErrorBox message={err} onRetry={() => { setErr(""); reload(); }} />}
    {!items && !err ? <><SkeletonCard /><SkeletonCard /></>
      : items && !visible.length ? (editing ? null : <EmptyCat />)
      : items && <>
        {fslot && visible.length >= 4 && <FilterPanel slotEl={fslot} cat={cat} loc={loc} cats={cats}
          count={count} onCat={setCat} onLoc={setLoc} />}
        {list}
      </>}
    {form && <ClueEditor key={form === "new" ? "new" : form.id} cc={id} name={name} clue={form === "new" ? null : form} onClose={() => setForm(null)}
      onSaved={() => { toast.push("Clue saved", { kind: "ok" }); setForm(null); reload(); }} />}
  </>;
}
