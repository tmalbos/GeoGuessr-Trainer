import { CATEGORY_ICONS, tagHue } from "./tags.js";

const p = { width: 14, height: 14, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true, className: "ico" };

// building / tree / globe
export const LocationIcon = ({ t }) => t === "Urban"
  ? <svg {...p}><path d="M4 21V5a1 1 0 0 1 1-1h8a1 1 0 0 1 1 1v16" /><path d="M14 10h5a1 1 0 0 1 1 1v10" /><path d="M8 8h2M8 12h2M8 16h2M17 14h1M17 18h1M3 21h18" /></svg>
  : t === "Rural"
  ? <svg {...p}><path d="M12 3l-5 7h3l-4 6h12l-4-6h3z" /><path d="M12 16v5" /></svg>
  : <svg {...p}><circle cx="12" cy="12" r="9" /><path d="M3 12h18" /><path d="M12 3a14 14 0 0 1 0 18a14 14 0 0 1 0-18" /></svg>;

/** Icon-only (name in the tooltip): building = Urban, tree = Rural, globe = Anywhere. */
export const LocationChip = ({ t }) =>
  <span className="chip loc" style={{ "--h": tagHue(t) }} title={t}><LocationIcon t={t} /><span className="sr">{t}</span></span>;

export const CategoryChip = ({ t }) =>
  <span className="chip neutral"><span aria-hidden="true">{CATEGORY_ICONS[t]}</span> {t}</span>;
