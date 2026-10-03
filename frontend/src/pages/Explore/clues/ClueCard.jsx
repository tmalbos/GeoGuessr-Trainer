import ClueMap from "./ClueMap.jsx";
import RichText from "./richText.jsx";
import { CategoryChip, LocationChip } from "./tagUi.jsx";
import { GENERAL_TAGS, LOCATION_TAGS } from "./tags.js";

const ico = { width: 16, height: 16, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true };
const Trash = () => <svg {...ico}><path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></svg>;
const Pencil = () => <svg {...ico}><path d="M12 20h9" /><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z" /></svg>;

export default function ClueCard({ cc, clue, editing, onDelete, onEdit }) {
  const hasMap = clue.scope === "reg";
  const map = hasMap && <ClueMap cc={cc} clue={clue} />;
  const loc = clue.tags.find((t) => LOCATION_TAGS.includes(t));
  const cat = clue.tags.find((t) => GENERAL_TAGS.includes(t));
  return <article className="clue">
    {editing && <div className="clue-tools">
      <button type="button" className="clue-edit" aria-label="Edit clue" title="Edit clue" onClick={() => onEdit(clue)}><Pencil /></button>
      <button type="button" className="clue-del" aria-label="Delete clue" title="Delete clue" onClick={() => onDelete(clue)}><Trash /></button>
    </div>}
    <div className="clue-media">
      {clue.image
        ? <>
            <img src={`/api/clues/${cc}/files/${clue.image}`} alt="" loading="lazy" />
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
  </article>;
}
