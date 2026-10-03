import { useEffect, useRef, useState } from "react";
import ConfirmDialog from "../../../components/ConfirmDialog.jsx";
import { api, errorText } from "../../../lib/api.js";
import FilePicker from "./FilePicker.jsx";
import RatingDots from "./RatingDots.jsx";
import { LocationIcon } from "./tagUi.jsx";
import { CATEGORY_ICONS, DEFAULT_LOCATION, GENERAL_TAGS, LOCATION_TAGS, RATINGS, VISIBILITIES, tagHue } from "./tags.js";

const GEO_ACCEPT = ".geojson,.json,.gpkg,.zip,.parquet,.kml";
const GEO_HINT = "Polygons, lines or points, detected automatically. GeoJSON, GeoPackage, zipped Shapefile, GeoParquet or KML";
const FILES = [
  { kind: "image", label: "Image", hint: "Shows what the clue looks like", accept: "image/*", scopes: ["nat", "reg"] },
  { kind: "csv", label: "CSV", hint: "GroupName,Level1,Level2,… (administrative areas)", accept: ".csv,text/csv", scopes: ["reg"] },
  { kind: "geometry", label: "Geometry", hint: GEO_HINT, accept: GEO_ACCEPT, scopes: ["reg"] },
];
const AREA_KINDS = ["csv", "geometry"];
const GEOM_FIELDS = ["polygons", "lines", "points"];

// kind "cat" = neutral chip with an emoji; anything else is a location (icon + color).
const Tag = ({ t, kind, on, caret, onClick, ...rest }) =>
  <button type="button" className={`tag ${kind === "cat" ? "neutral" : ""} ${on ? "on" : ""}`} style={{ "--h": tagHue(t) }}
    aria-pressed={caret ? undefined : on} onClick={onClick} {...rest}>
    {kind === "cat" ? <span aria-hidden="true">{CATEGORY_ICONS[t]}</span> : <LocationIcon t={t} />} {t}{caret && <span aria-hidden="true"> ▾</span>}
  </button>;

/** `clue` set = editing that clue; otherwise adding a new one. */
export default function ClueEditor({ cc, name, clue, onClose, onSaved }) {
  const [scope, setScope] = useState(clue?.scope ?? "nat");
  const [info, setInfo] = useState(clue?.info ?? "");
  const [location, setLocation] = useState(clue?.tags?.find((t) => LOCATION_TAGS.includes(t)) ?? "");
  const [general, setGeneral] = useState(clue?.tags?.find((t) => GENERAL_TAGS.includes(t)) ?? "");
  const [locTouched, setLocTouched] = useState(!!clue); // user picked "where it applies" by hand
  const [menu, setMenu] = useState(null); // null | "cat" | "loc"
  const [rating, setRating] = useState(() => Object.fromEntries(RATINGS.map(([k]) => [k, clue?.[k] != null ? String(clue[k]) : ""])));
  const [visibility, setVisibility] = useState(clue?.visibility ?? "visible");
  const [files, setFiles] = useState({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const [confirm, setConfirm] = useState(false);
  const area = useRef(null);
  const slot = useRef(null);

  const snapshot = () => JSON.stringify([scope, info, general, location, rating, visibility, Object.keys(files)]);
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

  const pickCategory = (t) => {
    setGeneral(t);
    if (!locTouched) setLocation(DEFAULT_LOCATION[t] ?? "Anywhere");
    setMenu(null);
  };
  const pickLocation = (t) => { setLocation(t); setLocTouched(true); setMenu(null); };
  const toggle = (m) => setMenu((cur) => (cur === m ? null : m));

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
  const problems = [];
  if (!info.trim()) problems.push("Write what the clue teaches.");
  if (!general || !location) problems.push("Add a tag.");
  if (scope === "nat" && !has("image")) problems.push("Add an image.");
  if (scope === "reg" && !AREA_KINDS.some(has)) problems.push("Add a CSV or a geometry file.");

  const save = async () => {
    setSaving(true); setErr("");
    const num = (v) => (v === "" ? null : Number(v));
    try {
      await api(clue ? `/clues/${cc}/${clue.id}` : `/clues/${cc}`, { method: clue ? "PUT" : "POST", body: {
        scope, info, tags: [location, general], visibility,
        frequency: num(rating.frequency), ease: num(rating.ease), reliability: num(rating.reliability),
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

      <label htmlFor="cl-info">Info</label>
      <div className="rt-bar" role="toolbar" aria-label="Text formatting">
        <button type="button" title="Bold" onClick={() => wrap("**")}><b>B</b></button>
        <button type="button" title="Italic" onClick={() => wrap("*")}><i>I</i></button>
        <button type="button" title="Underline" onClick={() => wrap("__")}><u>U</u></button>
        <button type="button" title="Link" onClick={() => wrap("[", "](https://)")}>Link</button>
      </div>
      <textarea id="cl-info" ref={area} rows={4} value={info} onChange={(e) => setInfo(e.target.value)} placeholder="What does this clue teach?" />

      <div className="rates-dots">
        {RATINGS.map(([k, label, lo, hi]) => <RatingDots key={k} label={label} lo={lo} hi={hi} value={rating[k]}
          onChange={(v) => setRating((r) => ({ ...r, [k]: v }))} />)}
      </div>
      <div className="vis-row">
        <label htmlFor="cl-vis">Visibility</label>
        <select id="cl-vis" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
          {VISIBILITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>
      </div>

      <h4>Files</h4>
      {slots.map((s) => <FilePicker key={s.kind} {...s} current={currentFor(s.kind)} value={files[s.kind]} onChange={(v) => setFiles((f) => ({ ...f, [s.kind]: v }))} />)}

      {err && <p className="err">{err}</p>}
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={requestClose}>Cancel</button>
        <button type="button" className="btn" title={problems[0]} disabled={saving || problems.length > 0} onClick={save}>{saving ? "Saving…" : "Save clue"}</button>
      </div>
      {problems.length > 0 && <p className="muted problems">{problems[0]}</p>}
    </div>
    {confirm && <ConfirmDialog title={clue ? "Discard your changes?" : "Discard this clue?"} body="What you entered won't be saved."
      confirmLabel="Discard" cancelLabel="Keep editing" danger onConfirm={onClose} onCancel={() => setConfirm(false)} />}
  </div>;
}
