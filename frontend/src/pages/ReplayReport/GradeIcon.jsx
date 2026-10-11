import { GRADES } from "./stages.js";

/** The chess-style icon of a grade (frontend/public/grades/<id>.png). */
export default function GradeIcon({ id, size = 22 }) {
  const g = GRADES[id];
  if (!g) return null;
  return <img className="gi" src={`/grades/${id}.png`} alt={g.label} title={g.label} width={size} height={size} />;
}
