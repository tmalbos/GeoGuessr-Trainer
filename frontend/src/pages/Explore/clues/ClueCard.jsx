import ClueMap from "./ClueMap.jsx";
import RichText from "./richText.jsx";
import { tagHue } from "./tags.js";

const Trash = () => <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M19 6l-1 14H6L5 6" /><path d="M10 11v6M14 11v6" /></svg>;

export default function ClueCard({ cc, clue, editing, onDelete }) {
  const hasMap = clue.scope === "reg";
  const map = hasMap && <ClueMap cc={cc} clue={clue} />;
  return <article className="clue">
    {editing && <button type="button" className="clue-del" aria-label="Delete clue" title="Delete clue" onClick={() => onDelete(clue)}><Trash /></button>}
    <div className="clue-media">
      {clue.image
        ? <>
            <img src={`/api/clues/${cc}/files/${clue.image}`} alt="" loading="lazy" />
            {hasMap && <div className="clue-ov">{map}</div>}
          </>
        : <div className="clue-full">{map}</div>}
    </div>
    <div className={`clue-body ${editing ? "has-del" : ""}`}>
      <div className="clue-tags">
        {clue.tags.map((t) => <span key={t} className="chip" style={{ "--h": tagHue(t) }}>{t}</span>)}
        {clue.visibility === "guide-only" && <span className="chip" style={{ "--h": 260 }}>Guide only</span>}
        {clue.visibility === "analysis-only" && <span className="chip" style={{ "--h": 0 }}>Analysis only (hidden)</span>}
      </div>
      <p className="clue-info"><RichText text={clue.info} /></p>
    </div>
  </article>;
}
