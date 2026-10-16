import { useEffect, useRef, useState } from "react";
import ConfirmDialog from "../../../components/ConfirmDialog.jsx";
import { api, errorText } from "../../../lib/api.js";
import FilePicker from "./FilePicker.jsx";
import RatingDots from "./RatingDots.jsx";
import RelatePicker from "./RelatePicker.jsx";
import { useCountries } from "./countries.js";
import { NOTE_TYPES, REL_LABEL, VERBS, joinNames, sameRel, uniq, verbFor } from "./relations.js";
import { LocationIcon } from "./tagUi.jsx";
import { CATEGORY_ICONS, DEFAULT_LOCATION, GENERAL_TAGS, LOCATION_TAGS, RATINGS, VISIBILITIES, tagHue } from "./tags.js";
import "./blur.css";
import "./relations.css";

const GEO_ACCEPT = ".geojson,.json,.gpkg,.zip,.parquet,.kml";
const GEO_HINT = "Polygons, lines or points, detected automatically. GeoJSON, GeoPackage, zipped Shapefile, GeoParquet or KML";
const FILES = [
  { kind: "image", label: "Image", hint: "Shows what the clue looks like", accept: "image/*", scopes: ["nat", "reg"] },
  { kind: "csv", label: "CSV", hint: "GroupName,Level1,Level2,… (administrative areas)", accept: ".csv,text/csv", scopes: ["reg"] },
  { kind: "geometry", label: "Geometry", hint: GEO_HINT, accept: GEO_ACCEPT, scopes: ["reg"] },
];
const AREA_KINDS = ["csv", "geometry"];
const GEOM_FIELDS = ["polygons", "lines", "points"];
const URL_OK = /^https?:\/\/\S+$/i;
const trunc = (s, n) => (s.length > n ? `${s.slice(0, n)}…` : s);

// kind "cat" = neutral chip with an emoji; anything else is a location (icon + color).
const Tag = ({ t, kind, on, caret, onClick, ...rest }) =>
  <button type="button" className={`tag ${kind === "cat" ? "neutral" : ""} ${on ? "on" : ""}`} style={{ "--h": tagHue(t) }}
    aria-pressed={caret ? undefined : on} onClick={onClick} {...rest}>
    {kind === "cat" ? <span aria-hidden="true">{CATEGORY_ICONS[t]}</span> : <LocationIcon t={t} />} {t}{caret && <span aria-hidden="true"> ▾</span>}
  </button>;

/** `clue` set = editing that clue; otherwise adding a new one. */
export default function ClueEditor({ cc, name, clue, onClose, onSaved }) {
  const { nameOf } = useCountries();
  const [scope, setScope] = useState(clue?.scope ?? "nat");
  const [info, setInfo] = useState(clue?.info ?? "");
  const [location, setLocation] = useState(clue?.tags?.find((t) => LOCATION_TAGS.includes(t)) ?? "");
  const [general, setGeneral] = useState(clue?.tags?.find((t) => GENERAL_TAGS.includes(t)) ?? "");
  const [locTouched, setLocTouched] = useState(!!clue); // user picked "where it applies" by hand
  const [menu, setMenu] = useState(null); // null | "cat" | "loc"
  const [rating, setRating] = useState(() => Object.fromEntries(RATINGS.map(([k]) => [k, clue?.[k] != null ? String(clue[k]) : ""])));
  const [visibility, setVisibility] = useState(clue?.visibility ?? "visible");
  const [blur, setBlur] = useState(!!clue?.blur);
  const [source, setSource] = useState(clue?.source ?? "");
  const [relations, setRelations] = useState(clue?.relations ?? []);
  const [notes, setNotes] = useState(clue?.relation_notes ?? {});
  const [badge, setBadge] = useState(clue?.badge ?? "");
  const [picking, setPicking] = useState(false);
  const [others, setOthers] = useState({}); // cc -> that country's clues, only to describe the relation chips
  const [files, setFiles] = useState({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(false);
  const area = useRef(null);
  const slot = useRef(null);
  const asked = useRef(new Set());

  const snapshot = () => JSON.stringify([scope, info, general, location, rating, visibility, blur,
    source, relations, notes, badge, Object.keys(files)]);
  const initial = useRef(snapshot());
  const dirty = snapshot() !== initial.current;
  const requestClose = () => (dirty ? setConfirm(true) : onClose());
  const closeRef = useRef(requestClose);
  closeRef.current = requestClose;

  const wrap = (before, after = before) => {
    const el = area.current;
    const { selectionStart: a, selectionEnd: b } = el;
    setInfo(info.slice(0, a) + before + info.slice(a, b) + after + info.slice(b));
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(a + before.length, b + before.length); });
  };

  // Escape closes the open tag menu first, then asks before throwing work away.
  useEffect(() => {
    const onKey = (e) => { if (e.key !== "Escape") return; if (menu) setMenu(null); else closeRef.current(); };
    const onDown = (e) => { if (menu && slot.current && !slot.current.contains(e.target)) setMenu(null); };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey); document.removeEventListener("mousedown", onDown); };
  }, [menu]);

  // Load the clues on the other end of each relation, so the chips can say which clue they point at.
  useEffect(() => {
    for (const code of new Set(relations.map((r) => r.cc))) {
      if (asked.current.has(code)) continue;
      asked.current.add(code);
      api(`/clues/${code}?all=true`).then((d) => setOthers((o) => ({ ...o, [code]: d }))).catch(() => {});
    }
  }, [relations]);

  const pickCategory = (t) => {
    setGeneral(t);
    if (!locTouched) setLocation(DEFAULT_LOCATION[t] ?? "Anywhere");
    setMenu(null);
  };
  const pickLocation = (t) => { setLocation(t); setLocTouched(true); setMenu(null); };
  const toggle = (m) => setMenu((cur) => (cur === m ? null : m));

  const present = (t) => relations.some((r) => r.type === t);
  const note = (t) => ({ verb: "uses", text: "", ...notes[t] });
  const setNote = (t, patch) => setNotes((n) => ({ ...n, [t]: { verb: "uses", text: "", ...n[t], ...patch } }));
  const addRelation = (r) => { setRelations((rs) => (rs.some((x) => sameRel(x, r)) ? rs : [...rs, r])); setPicking(false); };
  const removeRelation = (r) => setRelations((rs) => rs.filter((x) => !sameRel(x, r)));
  const chipText = (r) => {
    const snippet = others[r.cc]?.find((c) => c.id === r.id)?.info;
    const head = r.type === "group" ? "Group" : `${REL_LABEL[r.type]} · ${nameOf(r.cc)}`;
    return snippet ? `${head} — ${trunc(snippet, 32)}` : head;
  };

  // What the clue already stores (edit mode), so required files don't have to be re-uploaded.
  const currentFor = (kind) => {
    if (!clue) return null;
    if (kind === "image" && clue.image) return { src: `/api/clues/${cc}/files/${clue.image}`, text: "Current image" };
    if (kind === "csv" && clue.csv) return { text: `Current CSV${clue.csv_rows ? ` (${clue.csv_rows.length - 1} rows)` : ""}` };
    if (kind === "geometry") { const g = GEOM_FIELDS.find((k) => clue[k]); if (g) return { text: `Current geometry (${g})` }; }
    return null;
  };

  const slots = FILES.filter((f) => f.scopes.includes(scope));
  const has = (kind) => !!files[kind] || !!currentFor(kind);
  const hasImage = has("image");
  const problems = [];
  if (!info.trim()) problems.push("Write what the clue teaches.");
  if (!general || !location) problems.push("Add a tag.");
  if (scope === "nat" && !hasImage) problems.push("Add an image.");
  if (scope === "reg" && !AREA_KINDS.some(has)) problems.push("Add a CSV or a geometry file.");
  if (source.trim() && !URL_OK.test(source.trim())) problems.push("The source must be a link starting with http:// or https://.");
  for (const t of NOTE_TYPES) if (present(t) && !note(t).text.trim()) problems.push(`Finish the "${REL_LABEL[t]}" sentence.`);

  const save = async () => {
    setSaving(true); setErr("");
    const num = (v) => (v === "" ? null : Number(v));
    try {
      await api(clue ? `/clues/${cc}/${clue.id}` : `/clues/${cc}`, { method: clue ? "PUT" : "POST", body: {
        scope, info, tags: [location, general], visibility, blur: hasImage && blur,
        frequency: num(rating.frequency), ease: num(rating.ease), reliability: num(rating.reliability),
        source: source.trim(),
        relations,
        relation_notes: Object.fromEntries(NOTE_TYPES.filter(present).map((t) => [t, note(t)])),
        badge: badge || null,
        files: Object.fromEntries(slots.filter((s) => files[s.kind]).map((s) =>
          [s.kind === "geometry" ? files.geometry.kind : s.kind, files[s.kind].token])),
      } });
      onSaved();
    } catch (e) { setErr(errorText(e)); setSaving(false); }
  };

  return <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) requestClose(); }}>
    <div className="modal" role="dialog" aria-modal="true" aria-label={`${clue ? "Edit" : "Add"} clue for ${name}`}>
      <div className="modal-head">
        <h3>{clue ? "Edit clue" : "Add clue"} · {name}</h3>
        <div className="tag-slot" ref={slot}>
          {general
            ? <div className="tag-pair">
                <Tag t={general} kind="cat" on caret aria-haspopup="true" aria-expanded={menu === "cat"} onClick={() => toggle("cat")} />
                <Tag t={location} kind="loc" on caret aria-haspopup="true" aria-expanded={menu === "loc"} onClick={() => toggle("loc")} />
              </div>
            : <button type="button" className="tag-empty" aria-label="Add tag" aria-haspopup="true" aria-expanded={menu === "cat"}
                onClick={() => toggle("cat")}>+ tag</button>}
          {menu === "cat" && <div className="tag-pop" role="group" aria-label="Category">
            {GENERAL_TAGS.map((t) => <Tag key={t} t={t} kind="cat" on={general === t} onClick={() => pickCategory(t)} />)}
          </div>}
          {menu === "loc" && <div className="tag-pop small" role="group" aria-label="Where it applies">
            {LOCATION_TAGS.map((t) => <Tag key={t} t={t} kind="loc" on={location === t} onClick={() => pickLocation(t)} />)}
          </div>}
        </div>
      </div>
      <div className="tabs" role="group" aria-label="Scope">
        {[["nat", "National"], ["reg", "Regional"]].map(([k, l]) =>
          <button key={k} type="button" aria-pressed={scope === k} className={scope === k ? "on" : ""} disabled={!!clue && scope !== k} onClick={() => setScope(k)}>{l}</button>)}
      </div>

      {slots.map((s) => <FilePicker key={s.kind} {...s} current={currentFor(s.kind)} value={files[s.kind]} onChange={(v) => setFiles((f) => ({ ...f, [s.kind]: v }))}
        extra={s.kind === "image" && hasImage ? <label className="chk-row" htmlFor="cl-blur">
          <input id="cl-blur" type="checkbox" checked={blur} onChange={(e) => setBlur(e.target.checked)} />Blur</label> : null} />)}

      <label htmlFor="cl-info">Info</label>
      <div className="rt-bar" role="toolbar" aria-label="Text formatting">
        <button type="button" title="Bold" onClick={() => wrap("**")}><b>B</b></button>
        <button type="button" title="Italic" onClick={() => wrap("*")}><i>I</i></button>
        <button type="button" title="Underline" onClick={() => wrap("__")}><u>U</u></button>
        <button type="button" title="Link" onClick={() => wrap("[", "](https://)")}>Link</button>
      </div>
      <textarea id="cl-info" ref={area} rows={4} value={info} onChange={(e) => setInfo(e.target.value)} placeholder="What does this clue teach?" />

      <div className="rel-sec">
        <div className="rel-head">
          <label>Relations</label>
          <button type="button" className="btn ghost" onClick={() => setPicking(true)}>+ Relate</button>
        </div>
        {relations.length > 0 && <div className="rel-chips">
          {relations.map((r) => <span key={`${r.type}-${r.cc}-${r.id}`} className={`rel-chip ${r.type}`} title={others[r.cc]?.find((c) => c.id === r.id)?.info}>
            <span>{chipText(r)}</span>
            <button type="button" aria-label="Remove relation" onClick={() => removeRelation(r)}>×</button>
          </span>)}
        </div>}
        {NOTE_TYPES.filter(present).map((t) => {
          const codes = uniq(relations.filter((r) => r.type === t).map((r) => r.cc));
          const n = note(t);
          return <div className={`rel-note ${t}`} key={t}>
            <span>{joinNames(codes.map(nameOf))}</span>
            <select aria-label={`${REL_LABEL[t]} verb`} value={n.verb} onChange={(e) => setNote(t, { verb: e.target.value })}>
              {VERBS.map((v) => <option key={v} value={v}>{verbFor(v, codes.length)}</option>)}
            </select>
            {t === "similar_to" && <span>similar</span>}
            <input type="text" aria-label={`${REL_LABEL[t]} sentence`} value={n.text} placeholder="…finish the sentence"
              onChange={(e) => setNote(t, { text: e.target.value })} />
          </div>;
        })}
      </div>

      <div className="rates-dots">
        {RATINGS.map(([k, label, lo, hi]) => <RatingDots key={k} label={label} lo={lo} hi={hi} value={rating[k]}
          onChange={(v) => setRating((r) => ({ ...r, [k]: v }))} />)}
      </div>
      <div className="vis-row">
        <label htmlFor="cl-vis">Visibility</label>
        <select id="cl-vis" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          {VISIBILITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </div>
      <div className="vis-row">
        <label htmlFor="cl-badge">Uniqueness badge</label>
        <select id="cl-badge" value={badge} onChange={(e) => setBadge(e.target.value)}>
          <option value="">None</option>
          <option value="subcontinent">Subcontinent unique</option>
          <option value="world">World unique</option>
        </select>
      </div>

      <label htmlFor="cl-src">Source</label>
      <input id="cl-src" type="text" value={source} onChange={(e) => setSource(e.target.value)}
        placeholder="Link (Wikipedia, Plonkit, Reddit, RMRG, Discord…)" />

      {err && <p className="err">{err}</p>}
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={requestClose}>Cancel</button>
        <button type="button" className="btn" title={problems[0]} disabled={saving || problems.length > 0} onClick={save}>{saving ? "Saving…" : "Save clue"}</button>
      </div>
      {problems.length > 0 && <p className="muted problems">{problems[0]}</p>}
    </div>
    {picking && <RelatePicker cc={cc} clueId={clue?.id ?? null} tags={{ general, location }} taken={relations}
      onPick={addRelation} onClose={() => setPicking(false)} />}
    {confirm && <ConfirmDialog title={clue ? "Discard your changes?" : "Discard this clue?"} body="What you entered won't be saved."
      confirmLabel="Discard" cancelLabel="Keep editing" danger onConfirm={onClose} onCancel={() => setConfirm(false)} />}
  </div>;
}
