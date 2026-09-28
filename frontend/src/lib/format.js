export const MATCH_TYPE_LABELS = { daily: "Daily", challenge: "Challenge", duel: "Duel" };
export const MOVE_TYPE_LABELS = { moving: "Moving", no_move: "No Move", nmpz: "NMPZ" };
export const tlKey = (v) => (v == null ? "null" : String(v));
export const tlLabel = (v) => (v == null ? "No limit" : `${Math.round(v / 60)} min`);

export const time = (s) => (s == null ? "—" : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
export const num = (n) => (n == null ? "—" : n.toLocaleString());
export const tone = (score) => `hsl(${Math.max(0, Math.min(1, (score - 2000) / 3000)) * 150} 60% 38%)`;
