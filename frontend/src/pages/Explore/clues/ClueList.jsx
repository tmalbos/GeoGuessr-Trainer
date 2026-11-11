import { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import ErrorBox from "../../../components/ErrorBox.jsx";
import { SkeletonCard } from "../../../components/Skeleton.jsx";
import { useToast } from "../../../components/Toast.jsx";
import { api, errorText } from "../../../lib/api.js";
import { streetViewAvailable } from "../../../lib/mapProviders.js";
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
const MIN_GROUP = 5; // a regional category gets its own group only with more clues than this
const Funnel = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 4h18l-7 8v6l-4 2v-8z" /></svg>;
const ImportIcon = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3v10" /><path d="m8 9 4 4 4-4" />
  <path d="M4 17v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></svg>;

/** The tools drawn into the page's side panel (`slotEl`): the filter toggle on the left and, in edit mode,
 *  "Import from…" and "+ Add clue" on the far right of the same line. The filter chips open in a box below the
 *  line; the import sources open in a small dropdown under their button. A click outside (or Escape) closes both. */
function SideTools({ slotEl, showFilter, editing, onAdd, onImport, cat, loc, scope, fromReport, cats, count, onCat, onLoc, onScope, onReport, onClear, sections, onJump }) {
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
      {showFilter && <div className="cf-filter-wrap">
        <button type="button" className="cf-toggle" aria-expanded={open}
          aria-label="Filter the clues" title="Filter the clues" onClick={() => { setSrcOpen(false); setOpen((o) => !o); }}>
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
            <div className="clue-chips" role="group" aria-label="Filter by scope">
              {[["nat", "National"], ["reg", "Regional"]].map(([k, l]) => <button key={k} type="button" className={`fchip ${scope === k ? "on" : ""}`}
                aria-pressed={scope === k} onClick={() => onScope(scope === k ? "" : k)}>{l}</button>)}
            </div>
            {fromReport && <div className="clue-chips"><button type="button" className="fchip on" aria-pressed="true" onClick={onReport}>From report</button></div>}
            {active > 0 && <button type="button" className="link-btn" onClick={onClear}>Clear</button>}
          </div>
        </div>}
      </div>}
      {sections.length > 1 && <nav className="cf-jump" aria-label="Jump to a group">
        {sections.map((s) => <button key={s.key} type="button" className="cf-jump-item" title={`${s.label} · ${s.items.length} clue${s.items.length === 1 ? "" : "s"}`} onClick={() => onJump(s.key)}>
          <span className="cf-jump-ico" aria-hidden="true">{s.icon}</span><span className="cf-jump-name">{s.label}</span>
        </button>)}
      </nav>}
      {editing && <div className="cf-right">
        <div className="cf-import-wrap">
          <button type="button" className="btn ghost cf-import" aria-haspopup="menu" aria-expanded={srcOpen}
            aria-label="Import clues" title="Import clues"
            onClick={() => { setOpen(false); setSrcOpen((o) => !o); }}><ImportIcon /></button>
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
  </>, slotEl);
}

/** `query` (from the URL) sets the filters, e.g. from a replay report action:
 *    scope=nat|reg   National / Regional filter
 *    loc=Urban|Rural location filter (ignored when ids is given)
 *    ids=a,b,c       "From report" filter: only those clues */
const idsOf = (q) => (q ? new Set(q.split(",")) : null);
const scopeOf = (q) => (q === "nat" || q === "reg" ? q : "");
const locOfQuery = (q, ids) => (!ids && LOCATION_TAGS.includes(q) ? q : "");

export default function ClueList({ id, name, editing, collapsed, query }) {
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
  const [imp, setImp] = useState(null); // running import: { token, index, want, total, sections, clue, file, pending, err }
  const pending = useRef(new Map()); // key -> { cc, clueId }, deletes waiting out their undo window
  const [fslot, setFslot] = useState(null); // the side panel element the tools render into
  const itemsRef = useRef(null);
  itemsRef.current = items;
  const impRef = useRef(null);
  impRef.current = imp;
  const addAt = useRef(null); // where the list was on screen when "Add clue" was pressed

  useEffect(() => { setFslot(document.getElementById("clue-filter-slot")); }, []);

  useEffect(() => {
    let off = false;
    // The old list stays on screen while this runs: blanking it first shrank the column and yanked
    // the scroll position back to the top every time a clue was saved or deleted.
    api(`/clues/${id}`).then((d) => {
        if (off) return;
        setItems(d);
        setErr("");
        // This list is the truth about whether the country has clues: tell the world map without refetching.
        setHasClues(id, d.length > 0);
      }).catch((e) => { if (!off) { setErr(errorText(e)); setItems(null); } });
    return () => { off = true; };
  }, [id, version]);

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

  // ── Import from Plonkit: one clue at a time; the text is instant, the image downloads on demand ──
  // `sections` = [{ step, title, index }] from the start call: the guide's sections and where each one begins.
  const loadImport = async (token, index, total, sections, clues) => {
    const mine = () => impRef.current?.token === token && impRef.current.want === index;
    const set = (patch) => { impRef.current = { ...impRef.current, ...patch }; setImp(impRef.current); };
    if (impRef.current?.token !== token) impRef.current = { clue: null, file: null, pending: false };
    set({ token, want: index, total, sections, err: "", ...(clues ? { clues } : {}) });
    try {
      const d = await api(`/import/plonkit/${token}/${index}`);
      if (!mine()) return;
      const sv = !!d.link && streetViewAvailable();
      const needsImage = d.zone || !sv; // before the Regional sections the Street View link is enough
      set({ index, clue: { ...d, scope: d.zone && sv ? "reg" : "nat", street_view_url: sv ? d.link : null }, file: null, pending: needsImage });
      if (!needsImage) return;
      const r = await fetch(`/api/import/plonkit/${token}/${index}/image`);
      if (!mine()) return;
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const b = await r.blob();
      if (mine()) set({ file: new File([b], "plonkit.png", { type: b.type || "image/png" }), pending: false });
    } catch (e) {
      if (mine()) set({ err: errorText(e), pending: false });
    }
  };

  const startImport = async () => {
    toast.push("Looking for the Plonkit guide…", { ms: 2500 });
    try {
      const r = await api("/import/plonkit/start", { method: "POST", body: { cc: id } });
      setForm(null);
      loadImport(r.token, 0, r.total, r.sections ?? [], r.clues ?? []);
    } catch (e) { toast.push(errorText(e), { kind: "err", ms: 6000 }); }
  };

  const advance = () => {
    const p = impRef.current;
    if (!p) return;
    if (p.want + 1 >= p.total) { toast.push("Import finished", { kind: "ok" }); stopImport(); return; }
    loadImport(p.token, p.want + 1, p.total, p.sections);
  };

  // "Skip to <section>": jump to the first clue of a later section, leaving the rest of this one (and any in between) out.
  const skipTo = (section) => {
    const p = impRef.current;
    if (!p) return;
    loadImport(p.token, section.index, p.total, p.sections);
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

  // The server appends new clues at the end. Move the new one into ITS OWN section (national, or its category),
  // one clue further down than the last clue above the middle of the screen: right after the first clue that is
  // below that line. If there is none below, it goes after the last one; if none is above, after the first one.
  const placeNew = async (before) => {
    const at = addAt.current;
    addAt.current = null;
    if (!at) return;
    try {
      const fresh = await api(`/clues/${id}`);
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
      const anchor = section[Math.min(above.length, section.length - 1)]; // one more below, when there is one
      const ids = fresh.map((c) => c.id).filter((i) => i !== entry.id);
      ids.splice(ids.indexOf(anchor.id) + 1, 0, entry.id);
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

  // Clues are shown unless deleted (still undoable) or filtered out by the report link.
  const visible = (items ?? []).filter((c) => !hidden.has(`${id}/${c.id}`));
  const cats = GENERAL_TAGS.filter((t) => visible.some((c) => catOf(c) === t));
  const count = (t) => visible.filter((c) => catOf(c) === t).length;
  const filtered = visible.filter((c) => (!cat || catOf(c) === cat) && locMatches(c, loc)
    && (!scope || c.scope === scope) && (!ids || ids.has(c.id)));
  // `key` = the block the card is shown in (clues can only be dragged inside their own block).
  const card = (c, key, opts = {}) => <ClueCard key={c.id} cc={id} clue={c} editing={editing} onDelete={remove} onEdit={setForm}
    dragging={drag?.id === c.id} over={over === c.id} showTags={opts.showTags !== false} showSource={opts.showSource !== false} collapsed={collapsed}
    onGrab={(e) => grab(e, c, key)} onOver={(e) => hover(e, c, key)} onDrop={(e) => drop(e, c, key)} onRelease={release} />;
  // Clues joined by a "group" relation are drawn together inside one box. The tags of the first clue and
  // the source(s) of the whole group are shown once, for the box, instead of repeating on every clue.
  const flow = (nodes) => (collapsed ? <div className="clue-flow">{nodes}</div> : nodes);
  const blocks = (arr, key) => flow(rawBlocks(arr, key));
  const rawBlocks = (arr, key) => groupBlocks(arr).map((b) => {
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

  // Clues are ordered by medal score (best first; unscored last, keeping their saved order among themselves).
  // National clues are one group. Regional clues get a group per category with more than MIN_GROUP clues;
  // all the other regional clues share one "Regional" group, shown before the category groups.
  const byScore = (a, b) => (b.score ?? -1) - (a.score ?? -1);
  const sections = []; // { key, icon, label, items }
  if (filtered.length) {
    const nat = filtered.filter((c) => c.scope !== "reg").sort(byScore);
    const reg = filtered.filter((c) => c.scope === "reg").sort(byScore);
    const big = GENERAL_TAGS.filter((t) => reg.filter((c) => catOf(c) === t).length >= MIN_GROUP);
    if (nat.length) sections.push({ key: "nat", icon: "🌍", label: "National", items: nat });
    const rest = reg.filter((c) => !big.includes(catOf(c)));
    if (rest.length) sections.push({ key: "reg", icon: "📍", label: "Regional", items: rest });
    for (const t of big) sections.push({ key: t, icon: CATEGORY_ICONS[t], label: t, items: reg.filter((c) => catOf(c) === t) });
  }
  const heading = (s) => <h4 className="clue-group" data-group={s.key}>{s.icon && <span aria-hidden="true">{s.icon}</span>} {s.label} <span className="count">{s.items.length}</span></h4>;
  const list = !sections.length ? null
    : sections.length === 1 ? blocks(sections[0].items, sections[0].key)
      : sections.map((s) => <section key={s.key}>{heading(s)}{blocks(s.items, s.key)}</section>);
  // Scrolls the page so the group's title is at the top of the view.
  const jump = (key) => {
    const el = document.querySelector(`[data-group="${CSS.escape(key)}"]`);
    const box = el?.closest(".cp");
    if (!box) return;
    const gap = () => el.getBoundingClientRect().top - box.getBoundingClientRect().top;
    box.querySelectorAll('img[loading="lazy"]').forEach((i) => { i.loading = "eager"; });
    let stop = false;
    box.addEventListener("wheel", () => { stop = true; }, { once: true, passive: true });
    const t0 = performance.now();
    const step = (now) => {
      if (stop) return;
      const g = gap();
      if (Math.abs(g) < 1 || now - t0 > 2500) { box.scrollTop += g; return; }
      box.scrollTop += Math.sign(g) * Math.max(1, Math.abs(g) * 0.15);
      requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  };

  const showFilter = !!items && (visible.length >= 4 || !!(cat || loc || scope || ids || qScope || qLoc || qIds));
  const ic = imp?.clue;
  // What the Skip dropdown offers: the clues after the one on screen, each section's name before its first clue.
  const skipItems = [];
  for (const c of imp ? (imp.clues ?? []).filter((x) => x.index > imp.index) : []) {
    const sec = (imp.sections ?? []).find((s) => s.index === c.index);
    if (sec) skipItems.push({ key: `s-${sec.step}`, label: sec.title, head: true, go: () => skipTo(sec) });
    skipItems.push({ key: `c-${c.index}`, label: c.title, category: c.category, go: () => skipTo(c) });
  }

  return <>
    {/* Normally the button lives in the side panel; this is only a fallback if that panel isn't there. */}
    {editing && !fslot && <div className="clue-bar">
      <button type="button" className="btn" onClick={startAdd}>+ Add clue</button>
    </div>}
    {fslot && (editing || showFilter || sections.length > 1) && <SideTools slotEl={fslot} showFilter={showFilter} editing={editing} onAdd={startAdd}
      onImport={startImport} cat={cat} loc={loc} scope={scope} fromReport={!!ids} cats={cats} count={count}
      onCat={setCat} onLoc={setLoc} onScope={setScope} onReport={() => setIds(null)} onClear={clear} sections={sections} onJump={jump} />}
    {err && <ErrorBox message={err} onRetry={() => { setErr(""); reload(); }} />}
    {!items && !err ? <><SkeletonCard /><SkeletonCard /></>
      : items && !visible.length ? (editing ? null : <EmptyCat />)
      : items && list}
    {form && <ClueEditor key={form === "new" ? "new" : form.id} cc={id} name={name} clue={form === "new" ? null : form} onClose={() => setForm(null)}
      onSaved={saved} />}
    {imp?.err && <div className="modal-back" style={{ zIndex: 130 }}><div className="modal">
      <ErrorBox message={imp.err} onRetry={() => loadImport(imp.token, imp.want, imp.total, imp.sections)} />
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={stopImport}>Close</button>
        <button type="button" className="btn" onClick={advance}>Skip</button>
      </div>
    </div></div>}
    {imp && ic && <ClueEditor key={`imp-${imp.token}-${ic.index}`} cc={id} name={name} clue={null}
      prefill={{
        scope: ic.scope, info: ic.info, general: ic.category, location: ic.location, source: ic.source,
        mediaText: ic.street_view_url, pending: !!imp.pending,
        areaFile: ic.scope === "reg" ? imp.file : null, imageFile: ic.scope === "reg" ? null : imp.file,
        progress: `Plonkit ${imp.index + 1} of ${imp.total}`,
      }}
      skipTo={skipItems}
      onClose={stopImport} onSaved={importSaved} onSkip={advance} />}
  </>;
}
