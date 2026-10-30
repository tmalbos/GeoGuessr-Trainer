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
import "./side-tools.css";

const catOf = (c) => c.tags.find((t) => GENERAL_TAGS.includes(t));
const locOf = (c) => c.tags.find((t) => LOCATION_TAGS.includes(t));
// Urban / Rural also show the "Anywhere" clues (they apply there too); Anywhere shows only itself.
const locMatches = (c, loc) => !loc || locOf(c) === loc || (loc !== "Anywhere" && locOf(c) === "Anywhere");
const UNDO_MS = 5000;
const SHOWN = ["visible", "guide-only"]; // what counts as "has clues" on the world map
const Funnel = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 4h18l-7 8v6l-4 2v-8z" /></svg>;

/** The tools drawn into the page's side panel (`slotEl`): the filter toggle on the left and, in edit mode,
 *  "Import from…" and "+ Add clue" on the far right of the same line. The filter chips open in a box below the
 *  line; the import sources open in a small dropdown under their button. A click outside (or Escape) closes both. */
function SideTools({ slotEl, showFilter, editing, onAdd, onImport, cat, loc, scope, fromReport, cats, count, onCat, onLoc, onScope, onReport, onClear }) {
  const [open, setOpen] = useState(false);
  const [srcOpen, setSrcOpen] = useState(false);
  const active = (cat ? 1 : 0) + (loc ? 1 : 0) + (scope ? 1 : 0) + (fromReport ? 1 : 0);
  const anyOpen = open || srcOpen;

  useEffect(() => {
    if (!anyOpen) return undefined;
    const onDown = (e) => {
      if (e.target.closest?.(".cf-toggle, .clue-filter, .cf-import-wrap")) return;
      setOpen(false); setSrcOpen(false);
    };
    const onKey = (e) => { if (e.key === "Escape") { setOpen(false); setSrcOpen(false); } };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [anyOpen]);

  return createPortal(<>
    <div className="cf-bar">
      {showFilter && <button type="button" className="cf-toggle" aria-expanded={open}
        aria-label="Filter the clues" title="Filter the clues" onClick={() => { setSrcOpen(false); setOpen((o) => !o); }}>
        <Funnel />
        {active > 0 && <span className="count">{active}</span>}
      </button>}
      {editing && <div className="cf-right">
        <div className="cf-import-wrap">
          <button type="button" className="btn ghost cf-import" aria-haspopup="menu" aria-expanded={srcOpen}
            onClick={() => { setOpen(false); setSrcOpen((o) => !o); }}>Import from… <span aria-hidden="true">▾</span></button>
          {srcOpen && <div className="cf-menu" role="menu" aria-label="Import source">
            <button type="button" role="menuitem" className="cf-menu-item" onClick={() => { setSrcOpen(false); onImport("plonkit"); }}>
              <img src="https://www.google.com/s2/favicons?domain=plonkit.net&sz=32" alt="" width="20" height="20"
                onError={(e) => { e.currentTarget.style.display = "none"; }} />
              <span><b>Plonkit</b><small>Go through this country's guide, clue by clue</small></span>
            </button>
          </div>}
        </div>
        <button type="button" className="btn cf-add" onClick={onAdd}>+ Add clue</button>
      </div>}
    </div>
    {showFilter && open && <div className="clue-filter">
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
        <div className="clue-chips" role="group" aria-label="Filter by scope">
          {[["nat", "National"], ["reg", "Regional"]].map(([k, l]) => <button key={k} type="button" className={`fchip ${scope === k ? "on" : ""}`}
            aria-pressed={scope === k} onClick={() => onScope(scope === k ? "" : k)}>{l}</button>)}
        </div>
        {fromReport && <div className="clue-chips"><button type="button" className="fchip on" aria-pressed="true" onClick={onReport}>From report</button></div>}
        {active > 0 && <button type="button" className="link-btn" onClick={onClear}>Clear</button>}
      </div>
    </div>}
  </>, slotEl);
}

/** `query` (from the URL) sets the filters, e.g. from a replay report action:
 *    scope=nat|reg   National / Regional filter
 *    loc=Urban|Rural location filter (ignored when ids is given)
 *    ids=a,b,c       "From report" filter: only those clues (analysis-only ones included) */
const idsOf = (q) => (q ? new Set(q.split(",")) : null);
const scopeOf = (q) => (q === "nat" || q === "reg" ? q : "");
const locOfQuery = (q, ids) => (!ids && LOCATION_TAGS.includes(q) ? q : "");

export default function ClueList({ id, name, editing, query }) {
  const toast = useToast();
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const [form, setForm] = useState(null); // null | "new" | a clue being edited
  const [version, setVersion] = useState(0);
  const [hidden, setHidden] = useState(() => new Set()); // deleted but still undoable
  const qScope = query?.get("scope") ?? "";
  const qLoc = query?.get("loc") ?? "";
  const qIds = query?.get("ids") ?? "";
  const [cat, setCat] = useState("");
  const [loc, setLoc] = useState(() => locOfQuery(qLoc, qIds));
  const [scope, setScope] = useState(() => scopeOf(qScope));
  const [ids, setIds] = useState(() => idsOf(qIds)); // the "From report" filter
  const [drag, setDrag] = useState(null); // { id, key } of the clue being dragged
  const [over, setOver] = useState(null); // id of the clue it is hovering
  const [imp, setImp] = useState(null); // running import: { token, index, total, clue, file, err }
  const pending = useRef(new Map()); // key -> { cc, clueId }, deletes waiting out their undo window
  const [fslot, setFslot] = useState(null); // the side panel element the tools render into
  const itemsRef = useRef(null);
  itemsRef.current = items;
  const impRef = useRef(null);
  impRef.current = imp;
  const addAt = useRef(null); // where the list was on screen when "Add clue" was pressed

  const wantAll = editing || !!ids; // analysis-only clues are needed to show the ones a report picked

  useEffect(() => { setFslot(document.getElementById("clue-filter-slot")); }, []);

  useEffect(() => {
    let off = false;
    // The old list stays on screen while this runs: blanking it first shrank the column and yanked
    // the scroll position back to the top every time a clue was saved or deleted.
    api(`/clues/${id}${wantAll ? "?all=true" : ""}`).then((d) => {
        if (off) return;
        setItems(d);
        setErr("");
        // This list is the truth about whether the country has clues: tell the world map without refetching.
        setHasClues(id, d.some((c) => SHOWN.includes(c.visibility ?? "visible")));
      }).catch((e) => { if (!off) { setErr(errorText(e)); setItems(null); } });
    return () => { off = true; };
  }, [id, wantAll, version]);

  const stopImport = useCallback(() => {
    const p = impRef.current;
    if (p) api(`/import/plonkit/${p.token}`, { method: "DELETE" }).catch(() => {});
    impRef.current = null;
    setImp(null);
  }, []);

  useEffect(() => { setItems(null); setErr(""); setCat(""); setLoc(""); setForm(null); stopImport(); }, [id]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (!editing) { setForm(null); stopImport(); } }, [editing]); // eslint-disable-line react-hooks/exhaustive-deps
  // The filters follow the URL (runs after the reset above, so the link wins).
  useEffect(() => {
    const ids0 = idsOf(qIds);
    setCat(""); setLoc(locOfQuery(qLoc, ids0)); setScope(scopeOf(qScope)); setIds(ids0);
  }, [id, qScope, qLoc, qIds]);

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

  // ── Import from Plonkit: one clue at a time; the server keeps the next two ready ──
  const loadImport = async (token, index, total) => {
    setImp({ token, index, total, clue: null, file: null, err: "" });
    try {
      const d = await api(`/import/plonkit/${token}/${index}`);
      let file = null;
      if (d.scope === "reg") {
        const r = await fetch(`/api/import/plonkit/${token}/${index}/image`);
        if (r.ok) { const b = await r.blob(); file = new File([b], "plonkit.png", { type: b.type || "image/png" }); }
      }
      setImp((p) => (p && p.token === token && p.index === index ? { ...p, clue: d, file } : p));
    } catch (e) {
      setImp((p) => (p && p.token === token && p.index === index ? { ...p, err: errorText(e) } : p));
    }
  };

  const startImport = async () => {
    toast.push("Looking for the Plonkit guide…", { ms: 2500 });
    try {
      const r = await api("/import/plonkit/start", { method: "POST", body: { cc: id } });
      setForm(null);
      loadImport(r.token, 0, r.total);
    } catch (e) { toast.push(errorText(e), { kind: "err", ms: 6000 }); }
  };

  const advance = () => {
    const p = impRef.current;
    if (!p) return;
    if (p.index + 1 >= p.total) { toast.push("Import finished", { kind: "ok" }); stopImport(); return; }
    loadImport(p.token, p.index + 1, p.total);
  };

  const importSaved = () => {
    toast.push("Clue saved", { kind: "ok" });
    reload();
    advance();
  };

  // ── Add clue: remember where the list is on screen, so the new clue lands near it instead of at the end ──
  const startAdd = () => {
    const ys = {}; // clue id -> vertical centre of its card on screen
    document.querySelectorAll("article.clue[data-clue-id]").forEach((el) => {
      const r = el.getBoundingClientRect();
      ys[el.dataset.clueId] = r.top + r.height / 2;
    });
    let ref = window.innerHeight / 2; // the middle of what is visible
    const box = document.querySelector(".cp-main");
    if (box) {
      const r = box.getBoundingClientRect();
      ref = (Math.max(r.top, 0) + Math.min(r.bottom, window.innerHeight)) / 2;
    }
    addAt.current = { ref, ys };
    setForm("new");
  };

  // The server appends new clues at the end. Move the new one right after the last clue of ITS OWN section
  // (national, or its category) that is above the middle of the screen; if the section starts below it, put it first.
  const placeNew = async (before) => {
    const at = addAt.current;
    addAt.current = null;
    if (!at) return;
    try {
      const fresh = await api(`/clues/${id}?all=true`);
      const known = new Set(before.map((c) => c.id));
      const entry = fresh.find((c) => !known.has(c.id));
      if (!entry) return;
      const isReg = entry.scope === "reg";
      const mine = catOf(entry);
      const section = fresh
        .filter((c) => c.id !== entry.id && (c.scope === "reg") === isReg && (!isReg || catOf(c) === mine) && at.ys[c.id] != null)
        .sort((a, b) => at.ys[a.id] - at.ys[b.id]);
      if (!section.length) return; // nothing to be near: the end is fine
      const above = section.filter((c) => at.ys[c.id] <= at.ref);
      const ids = fresh.map((c) => c.id).filter((i) => i !== entry.id);
      const idx = above.length ? ids.indexOf(above[above.length - 1].id) + 1 : ids.indexOf(section[0].id);
      ids.splice(idx, 0, entry.id);
      await api(`/clues/${id}/reorder`, { method: "POST", body: { ids } });
    } catch (ex) { toast.push(errorText(ex), { kind: "err", ms: 6000 }); }
  };

  const saved = async () => {
    const adding = form === "new";
    const before = itemsRef.current ?? [];
    toast.push("Clue saved", { kind: "ok" });
    setForm(null);
    if (adding) await placeNew(before);
    reload();
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

  // Analysis-only clues are fetched when a report link needs them, but only shown if that link picked them.
  const visible = (items ?? []).filter((c) => !hidden.has(`${id}/${c.id}`)
    && (editing || (c.visibility ?? "visible") !== "analysis-only" || !!ids?.has(c.id)));
  const cats = GENERAL_TAGS.filter((t) => visible.some((c) => catOf(c) === t));
  const count = (t) => visible.filter((c) => catOf(c) === t).length;
  const filtered = visible.filter((c) => (!cat || catOf(c) === cat) && locMatches(c, loc)
    && (!scope || c.scope === scope) && (!ids || ids.has(c.id)));
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
  const clear = () => { setCat(""); setLoc(""); setScope(""); setIds(null); };

  let list;
  if (!filtered.length) {
    list = null;
  } else {
    // National clues sit under one "National" subtitle (only shown when there are regional clues too).
    // Regional clues always sit under the subtitle of their category.
    const nat = filtered.filter((c) => c.scope !== "reg");
    const reg = filtered.filter((c) => c.scope === "reg");
    const regCats = GENERAL_TAGS.filter((t) => reg.some((c) => catOf(c) === t));
    const heading = (icon, label, n) => <h4 className="clue-group">{icon && <span aria-hidden="true">{icon}</span>} {label} <span className="count">{n}</span></h4>;
    list = <>
      {nat.length > 0 && (reg.length > 0
        ? <section>{heading("🌍", "National", nat.length)}{blocks(nat, "nat")}</section>
        : blocks(nat, "nat"))}
      {regCats.map((t) => {
        const g = reg.filter((c) => catOf(c) === t);
        return <section key={t}>{heading(CATEGORY_ICONS[t], t, g.length)}{blocks(g, t)}</section>;
      })}
      {blocks(reg.filter((c) => !catOf(c)), "reg")}
    </>;
  }

  const showFilter = !!items && (visible.length >= 4 || !!(cat || loc || scope || ids || qScope || qLoc || qIds));
  const ic = imp?.clue;

  return <>
    {/* Normally the button lives in the side panel; this is only a fallback if that panel isn't there. */}
    {editing && !fslot && <div className="clue-bar">
      <button type="button" className="btn" onClick={startAdd}>+ Add clue</button>
    </div>}
    {fslot && (editing || showFilter) && <SideTools slotEl={fslot} showFilter={showFilter} editing={editing} onAdd={startAdd}
      onImport={startImport} cat={cat} loc={loc} scope={scope} fromReport={!!ids} cats={cats} count={count}
      onCat={setCat} onLoc={setLoc} onScope={setScope} onReport={() => setIds(null)} onClear={clear} />}
    {err && <ErrorBox message={err} onRetry={() => { setErr(""); reload(); }} />}
    {!items && !err ? <><SkeletonCard /><SkeletonCard /></>
      : items && !visible.length ? (editing ? null : <EmptyCat />)
      : items && list}
    {form && <ClueEditor key={form === "new" ? "new" : form.id} cc={id} name={name} clue={form === "new" ? null : form} onClose={() => setForm(null)}
      onSaved={saved} />}
    {imp && !ic && <div className="modal-back"><div className="modal">
      {imp.err
        ? <>
            <ErrorBox message={imp.err} onRetry={() => loadImport(imp.token, imp.index, imp.total)} />
            <div className="modal-actions">
              <button type="button" className="btn ghost" onClick={stopImport}>Close</button>
              <button type="button" className="btn" onClick={advance}>Skip</button>
            </div>
          </>
        : <p className="muted">Loading clue {imp.index + 1} of {imp.total} from Plonkit…</p>}
    </div></div>}
    {imp && ic && <ClueEditor key={`imp-${imp.token}-${imp.index}`} cc={id} name={name} clue={null}
      prefill={{
        scope: ic.scope, info: ic.info, general: ic.category, location: ic.location, source: ic.source,
        image: ic.image, mediaText: ic.scope === "reg" ? ic.street_view_url : null,
        mediaError: ic.scope === "reg" ? ic.street_view_error : "", areaFile: imp.file,
        progress: `Plonkit ${imp.index + 1} of ${imp.total}`,
      }}
      onClose={stopImport} onSaved={importSaved} onSkip={advance} />}
  </>;
}
