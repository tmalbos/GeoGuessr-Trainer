import { Fragment, useEffect, useState } from "react";
import Flag from "../../../components/Flag.jsx";
import { api, errorText } from "../../../lib/api.js";
import ClueMap from "./ClueMap.jsx";
import RichText from "./richText.jsx";
import { CategoryChip, LocationChip } from "./tagUi.jsx";
import { GENERAL_TAGS, LOCATION_TAGS } from "./tags.js";
import { useCountries } from "./countries.js";
import { verbFor, relsOf, uniq } from "./relations.js";
import fileUrl from "./fileUrl.js";
import "./blur.css";
import "./editor.css";
import "./relations.css";

const ico = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const Trash = () => <svg {...ico}><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></svg>;
const Pencil = () => <svg {...ico}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>;
const Grip = () => <svg width="14" height="20" viewBox="0 0 14 20" fill="currentColor" aria-hidden="true">
  {[3, 10, 17].flatMap((y) => [3.5, 10.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.6" />))}</svg>;

// The site's own icon, taken from its domain. Hidden if it can't be loaded.
const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };

export function SourceChip({ source }) {
  const host = source ? hostOf(source) : "";
  if (!host) return null;
  return <a className="chip neutral src" href={source} target="_blank" rel="noopener noreferrer" title={host} aria-label={`Source: ${host}`}>
    <img src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`} alt="" width="16" height="16"
      onError={(e) => { e.currentTarget.style.display = "none"; }} /></a>;
}

// Image (with the regional map overlaid) or, without an image, just the map.
// An SVG stands for the region itself: alone it is the whole card picture, next to a photo it is the overlay.
// `clue.max_height` (px) caps the image: it keeps its proportions, so a tall image just gets narrower.
function Media({ cc, clue, children }) {
  const isReg = clue.scope === "reg";
  const hasSvg = !!clue.svg;
  const map = isReg && !clue.image && <ClueMap cc={cc} clue={clue} />;
  const limited = !!clue.image && !!clue.max_height;
  return <div className={`clue-media ${limited ? "limited" : ""}`} style={limited ? { "--mh": `${clue.max_height}px` } : undefined}>
    <div className="clue-photo">
      {clue.image
        ? <>
            <img src={fileUrl(cc, clue.image, clue.rev)} alt="" loading="lazy"
              className={clue.blur ? "clue-blur" : undefined} tabIndex={clue.blur ? 0 : undefined}
              title={clue.blur ? "Hover to reveal" : undefined} />
            {isReg && <div className="clue-ov">
              {hasSvg ? <img src={fileUrl(cc, clue.svg, clue.rev)} alt="" /> : <ClueMap cc={cc} clue={clue} />}
            </div>}
          </>
        : clue.svg
          ? <img src={fileUrl(cc, clue.svg, clue.rev)} alt="" loading="lazy" />
          : <div className="clue-full">{map}</div>}
    </div>
    {children}
  </div>;
}

// ⚠️ same_as, ℹ️ similar_to (automatic) + one manual uniqueness badge (chess "great" / "brilliant"). Three at most.
function Badges({ clue }) {
  const items = [];
  if (relsOf(clue, "same_as").length) items.push(<span key="w" className="cb" role="img" aria-label="Same clue in other countries" title="The same clue exists in other countries">⚠️</span>);
  if (relsOf(clue, "similar_to").length) items.push(<span key="i" className="cb" role="img" aria-label="Similar clue in other countries" title="A similar clue exists in other countries">ℹ️</span>);
  if (clue.badge === "subcontinent") {
    const t = clue.subcontinent ? `Unique to ${clue.subcontinent}` : "Subcontinent unique";
    items.push(<img key="s" className="cb-img" src="/grades/great.png" alt={t} title={t} />);
  }
  if (clue.badge === "world") items.push(<img key="u" className="cb-img" src="/grades/brilliant.png" alt="World unique" title="World unique" />);
  return items.length ? <div className="clue-badges">{items}</div> : null;
}

// "Chile and Peru use similar <text>" — country names open the other clue next to this one.
function RelationLine({ type, clue, open, nameOf, onToggle }) {
  const codes = uniq(relsOf(clue, type).map((r) => r.cc));
  if (!codes.length) return null;
  const note = { verb: "uses", text: "", ...clue.relation_notes?.[type] };
  return <p className={`clue-rel ${type}`}>
    {codes.map((c, i) => <Fragment key={c}>
      {i > 0 && (i === codes.length - 1 ? " and " : ", ")}
      <button type="button" className={`rel-c ${open.includes(c) ? "on" : ""}`} aria-pressed={open.includes(c)} onClick={() => onToggle(c)}>{nameOf(c)}</button>
    </Fragment>)}
    {" "}{verbFor(note.verb, codes.length)}{type === "similar_to" ? " similar" : ""} {note.text}
  </p>;
}

// The related clue(s) of one country, shown to the right of the card's own media.
function RelatedMedia({ cc, ids, name, onClose }) {
  const { idOf } = useCountries();
  const [list, setList] = useState(null);
  const [err, setErr] = useState("");
  useEffect(() => {
    let off = false;
    api(`/clues/${cc}?all=true`).then((d) => !off && setList(d.filter((c) => ids.includes(c.id)))).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [cc, ids.join()]); // eslint-disable-line react-hooks/exhaustive-deps
  return <div className="clue-rel-media">
    <div className="rel-cap">
      <Flag code={cc} width={20} /><b>{name}</b>
      <button type="button" aria-label={`Close ${name}`} onClick={onClose}>×</button>
    </div>
    {err ? <p className="err">{err}</p>
      : !list ? <p className="muted">Loading…</p>
      : list.length ? list.map((c) => <Media key={c.id} cc={idOf(cc)} clue={c} />)
      : <p className="muted">That clue no longer exists.</p>}
  </div>;
}

/** Drag props (edit mode): onGrab (grip drag start), onOver / onDrop (this card as a target), onRelease (drag end),
 *  dragging / over = visual states. showTags / showSource are off for clues inside a group: the group shows them once. */
export default function ClueCard({ cc, clue, editing, onDelete, onEdit, dragging, over, onGrab, onOver, onDrop, onRelease,
  showTags = true, showSource = true }) {
  const { nameOf } = useCountries();
  const [open, setOpen] = useState([]); // countries whose related clue is shown next to this one
  const toggleOpen = (c) => setOpen((o) => (o.includes(c) ? o.filter((x) => x !== c) : [...o, c]));
  const loc = clue.tags.find((t) => LOCATION_TAGS.includes(t));
  const cat = clue.tags.find((t) => GENERAL_TAGS.includes(t));
  const idsOf = (c) => (clue.relations ?? []).filter((r) => r.type !== "group" && r.cc === c).map((r) => r.id);
  const shown = open.filter((c) => idsOf(c).length); // a relation removed while open just disappears
  return <article className={`clue ${shown.length ? "expanded" : ""} ${dragging ? "dragging" : ""} ${over ? "over" : ""}`}
    style={shown.length ? { "--n": shown.length + 1 } : undefined}
    onDragOver={editing ? onOver : undefined} onDrop={editing ? onDrop : undefined}>
    {editing && <div className="clue-tools">
      <button type="button" className="clue-edit" aria-label="Edit clue" title="Edit clue" onClick={() => onEdit(clue)}><Pencil /></button>
      <button type="button" className="clue-del" aria-label="Delete clue" title="Delete clue" onClick={() => onDelete(clue)}><Trash /></button>
    </div>}
    {editing && <span className="clue-grip" draggable title="Drag to reorder" aria-label="Drag to reorder"
      onDragStart={onGrab} onDragEnd={onRelease}><Grip /></span>}
    <Media cc={cc} clue={clue}><Badges clue={clue} /></Media>
    {shown.map((c) => <RelatedMedia key={c} cc={c} ids={idsOf(c)} name={nameOf(c)} onClose={() => toggleOpen(c)} />)}
    <div className={`clue-body ${editing ? "has-tools" : ""}`}>
      <div className="clue-tags">
        {showTags && loc && <LocationChip t={loc} />}
        {showTags && cat && <CategoryChip t={cat} />}
        {clue.visibility === "guide-only" && <span className="chip" style={{ "--h": 260 }}>Guide only</span>}
        {clue.visibility === "analysis-only" && <span className="chip" style={{ "--h": 0 }}>Analysis only (hidden)</span>}
      </div>
      <p className="clue-info"><RichText text={clue.info} /></p>
      <RelationLine type="same_as" clue={clue} open={shown} nameOf={nameOf} onToggle={toggleOpen} />
      <RelationLine type="similar_to" clue={clue} open={shown} nameOf={nameOf} onToggle={toggleOpen} />
    </div>
    {showSource && <SourceChip source={clue.source} />}
  </article>;
}
