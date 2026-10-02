import { useEffect, useRef, useState } from "react";
import { api } from "../../../lib/api.js";
import FilePicker from "./FilePicker.jsx";
import { GENERAL_TAGS, LOCATION_TAGS, VISIBILITIES, tagHue } from "./tags.js";

const GEO_ACCEPT = ".geojson,.json,.gpkg,.zip,.parquet,.kml";
const GEO_HINT = "Polygons, lines or points, detected automatically. GeoJSON, GeoPackage, zipped Shapefile, GeoParquet or KML";
const FILES = [
  { kind: "image", label: "Image", hint: "Shows what the clue looks like", accept: "image/*", scopes: ["nat", "reg"] },
  { kind: "csv", label: "CSV", hint: "GroupName,Level1,Level2,… (administrative areas)", accept: ".csv,text/csv", scopes: ["reg"] },
  { kind: "geometry", label: "Geometry", hint: GEO_HINT, accept: GEO_ACCEPT, scopes: ["reg"] },
];
const AREA_KINDS = ["csv", "geometry"];
const RATINGS = [["frequency", "Frequency"], ["ease", "Ease"], ["reliability", "Reliability"]];

const Tag = ({ t, on, onClick }) => <button type="button" className={`tag ${on ? "on" : ""}`} style={{ "--h": tagHue(t) }} aria-pressed={on} onClick={onClick}>{t}</button>;

function Rating({ id, label, value, onChange }) {
  const set = value !== "";
  return <div className={`rate ${set ? "" : "unset"}`}>
    <label htmlFor={id}>{label}</label>
    <input id={id} type="range" min="1" max="10" step="1" value={set ? value : 5}
      onPointerDown={() => { if (!set) onChange("5"); }}
      onChange={(e) => onChange(e.target.value)} />
    <output htmlFor={id}>{set ? value : "—"}</output>
    <button type="button" className="rate-clear" aria-label={`Clear ${label}`} disabled={!set} onClick={() => onChange("")}>×</button>
  </div>;
}

export default function ClueEditor({ cc, name, onClose, onSaved }) {
  const [scope, setScope] = useState("nat");
  const [info, setInfo] = useState("");
  const [location, setLocation] = useState("");
  const [general, setGeneral] = useState("");
  const [rating, setRating] = useState({ frequency: "", ease: "", reliability: "" });
  const [visibility, setVisibility] = useState("visible");
  const [files, setFiles] = useState({});
  const [saving, setSaving] = useState(false);
  const [err, setErr] = useState("");
  const area = useRef(null);

  const wrap = (before, after = before) => {
    const el = area.current;
    const { selectionStart: a, selectionEnd: b } = el;
    setInfo(info.slice(0, a) + before + info.slice(a, b) + after + info.slice(b));
    requestAnimationFrame(() => { el.focus(); el.setSelectionRange(a + before.length, b + before.length); });
  };

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  const slots = FILES.filter((f) => f.scopes.includes(scope));
  const problems = [];
  if (!info.trim()) problems.push("Write what the clue teaches.");
  if (!location) problems.push("Pick Urban, Rural or Anywhere.");
  if (!general) problems.push("Pick a category tag.");
  if (scope === "nat" && !files.image) problems.push("Add an image.");
  if (scope === "reg" && !AREA_KINDS.some((k) => files[k])) problems.push("Add a CSV or a geometry file.");

  const save = async () => {
    setSaving(true); setErr("");
    const num = (v) => (v === "" ? null : Number(v));
    try {
      await api(`/clues/${cc}`, { method: "POST", body: {
        scope, info, tags: [location, general], visibility,
        frequency: num(rating.frequency), ease: num(rating.ease), reliability: num(rating.reliability),
        files: Object.fromEntries(slots.filter((s) => files[s.kind]).map((s) =>
          [s.kind === "geometry" ? files.geometry.kind : s.kind, files[s.kind].token])),
      } });
      onSaved();
    } catch (e) { setErr(e.message); setSaving(false); }
  };

  return <div className="modal-back" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
    <div className="modal" role="dialog" aria-modal="true" aria-label={`Add clue for ${name}`}>
      <h3>Add clue · {name}</h3>
      <div className="tabs" role="tablist">
        {[["nat", "National"], ["reg", "Regional"]].map(([k, l]) =>
          <button key={k} type="button" role="tab" aria-selected={scope === k} className={scope === k ? "on" : ""} onClick={() => setScope(k)}>{l}</button>)}
      </div>

      <label htmlFor="cl-info">Info</label>
      <div className="rt-bar" role="toolbar" aria-label="Text formatting">
        <button type="button" title="Bold" onClick={() => wrap("**")}><b>B</b></button>
        <button type="button" title="Italic" onClick={() => wrap("*")}><i>I</i></button>
        <button type="button" title="Underline" onClick={() => wrap("__")}><u>U</u></button>
        <button type="button" title="Link" onClick={() => wrap("[", "](https://)")}>Link</button>
      </div>
      <textarea id="cl-info" ref={area} rows={4} value={info} onChange={(e) => setInfo(e.target.value)} placeholder="What does this clue teach?" />

      <label>Where it applies</label>
      <div className="tags">{LOCATION_TAGS.map((t) => <Tag key={t} t={t} on={location === t} onClick={() => setLocation(t)} />)}</div>
      <label>Category</label>
      <div className="tags">{GENERAL_TAGS.map((t) => <Tag key={t} t={t} on={general === t} onClick={() => setGeneral(t)} />)}</div>

      <div className="rates">
        {RATINGS.map(([k, l]) => <Rating key={k} id={`cl-${k}`} label={l} value={rating[k]}
          onChange={(v) => setRating((r) => ({ ...r, [k]: v }))} />)}
      </div>
      <label htmlFor="cl-vis">Visibility</label>
      <select id="cl-vis" value={visibility} onChange={(e) => setVisibility(e.target.value)}>
        {VISIBILITIES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select>

      <h4>Files</h4>
      {slots.map((s) => <FilePicker key={s.kind} {...s} value={files[s.kind]} onChange={(v) => setFiles((f) => ({ ...f, [s.kind]: v }))} />)}

      {err && <p className="err">{err}</p>}
      <div className="modal-actions">
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn" title={problems[0]} disabled={saving || problems.length > 0} onClick={save}>{saving ? "Saving…" : "Save clue"}</button>
      </div>
    </div>
  </div>;
}
