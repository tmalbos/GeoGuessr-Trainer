import { useEffect, useRef, useState } from "react";
import { TYPES, useSync } from "../lib/sync.jsx";
import "./sync-button.css";

/** Split button: main part syncs, caret opens the match-type picker. Status lives in <SyncChip />. */
export default function SyncButton() {
  const { types, setTypes, running, start } = useSync();
  const [menu, setMenu] = useState(false);
  const box = useRef(null);

  useEffect(() => {
    if (!menu) return;
    const onDown = (e) => { if (box.current && !box.current.contains(e.target)) setMenu(false); };
    const onKey = (e) => { if (e.key === "Escape") setMenu(false); };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => { document.removeEventListener("mousedown", onDown); document.removeEventListener("keydown", onKey); };
  }, [menu]);

  const toggle = (k) => setTypes((t) => (t.includes(k) ? t.filter((x) => x !== k) : [...t, k]));

  return <div className="split" ref={box}>
    <button type="button" className="btn split-main" onClick={() => { setMenu(false); start(); }} disabled={running || !types.length}>
      <span className={`split-icon ${running ? "spin" : ""}`} aria-hidden="true">↻</span>
      {running ? "Syncing…" : "Sync games"}
      {!running && types.length < TYPES.length && types.length > 0 && <span className="split-badge">{types.length}/{TYPES.length}</span>}
    </button>
    <button type="button" className="btn split-caret" aria-haspopup="true" aria-expanded={menu}
      aria-label="Choose what to sync" disabled={running} onClick={() => setMenu((o) => !o)}>{menu ? "▲" : "▼"}</button>
    {menu && <div className="split-menu" role="group" aria-label="Match types to sync">
      <h4>Sync these</h4>
      {TYPES.map(([k, label]) => <label className="split-option" key={k}>
        <input type="checkbox" checked={types.includes(k)} onChange={() => toggle(k)} />{label}
      </label>)}
      {!types.length && <p className="split-hint">Pick at least one type.</p>}
    </div>}
  </div>;
}
