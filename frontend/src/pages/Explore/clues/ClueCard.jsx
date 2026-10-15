import ClueMap from "./ClueMap.jsx";
import RichText from "./richText.jsx";
import { CategoryChip, LocationChip } from "./tagUi.jsx";
import { GENERAL_TAGS, LOCATION_TAGS } from "./tags.js";
import "./blur.css";

const ico = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const Trash = () => <svg {...ico}><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></svg>;
const Pencil = () => <svg {...ico}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>;
const Grip = () => <svg width="14" height="20" viewBox="0 0 14 20" fill="currentColor" aria-hidden="true">
  {[3, 10, 17].flatMap((y) => [3.5, 10.5].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.6" />))}</svg>;

// The site's own icon, taken from its domain. Hidden if it can't be loaded.
const hostOf = (url) => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } };

/** Drag props (edit mode): onGrab (grip drag start), onOver / onDrop (this card as a target), onRelease (drag end),
 *  dragging / over = visual states. */
export default function ClueCard({ cc, clue, editing, onDelete, onEdit, dragging, over, onGrab, onOver, onDrop, onRelease }) {
  const hasMap = clue.scope === "reg";
  const map = hasMap && <ClueMap cc={cc} clue={clue} />;
  const loc = clue.tags.find((t) => LOCATION_TAGS.includes(t));
  const cat = clue.tags.find((t) => GENERAL_TAGS.includes(t));
  const host = clue.source ? hostOf(clue.source) : "";
  return <article className={`clue ${dragging ? "dragging" : ""} ${over ? "over" : ""}`}
    onDragOver={editing ? onOver : undefined} onDrop={editing ? onDrop : undefined}>
    {editing && <div className="clue-tools">
      <button type="button" className="clue-edit" aria-label="Edit clue" title="Edit clue" onClick={() => onEdit(clue)}><Pencil /></button>
      <button type="button" className="clue-del" aria-label="Delete clue" title="Delete clue" onClick={() => onDelete(clue)}><Trash /></button>
    </div>}
    {editing && <span className="clue-grip" draggable title="Drag to reorder" aria-label="Drag to reorder"
      onDragStart={onGrab} onDragEnd={onRelease}><Grip /></span>}
    <div className="clue-media">
      {clue.image
        ? <>
            <img src={`/api/clues/${cc}/files/${clue.image}`} alt="" loading="lazy"
              className={clue.blur ? "clue-blur" : undefined} tabIndex={clue.blur ? 0 : undefined}
              title={clue.blur ? "Hover to reveal" : undefined} />
            {hasMap && <div className="clue-ov">{map}</div>}
          </>
        : <div className="clue-full">{map}</div>}
    </div>
    <div className={`clue-body ${editing ? "has-tools" : ""}`}>
      <div className="clue-tags">
        {loc && <LocationChip t={loc} />}
        {cat && <CategoryChip t={cat} />}
        {clue.visibility === "guide-only" && <span className="chip" style={{ "--h": 260 }}>Guide only</span>}
        {clue.visibility === "analysis-only" && <span className="chip" style={{ "--h": 0 }}>Analysis only (hidden)</span>}
      </div>
      <p className="clue-info"><RichText text={clue.info} /></p>
    </div>
    {host && <a className="chip neutral src" href={clue.source} target="_blank" rel="noopener noreferrer" title={host} aria-label={`Source: ${host}`}>
      <img src={`https://www.google.com/s2/favicons?domain=${encodeURIComponent(host)}&sz=32`} alt="" width="16" height="16"
        onError={(e) => { e.currentTarget.style.display = "none"; }} /></a>}
  </article>;
}
