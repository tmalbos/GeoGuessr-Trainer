import { useEffect, useState } from "react";
import { api } from "../../../lib/api.js";
import ClueCard from "./ClueCard.jsx";
import ClueEditor from "./ClueEditor.jsx";
import "./clues.css";

export default function ClueList({ id, name, editing }) {
  const [items, setItems] = useState(null);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState(false);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let off = false;
    setItems(null); setErr("");
    api(`/clues/${id}${editing ? "?all=true" : ""}`).then((d) => !off && setItems(d)).catch((e) => !off && setErr(e.message));
    return () => { off = true; };
  }, [id, editing, version]);

  useEffect(() => { if (!editing) setAdding(false); }, [editing]);

  const reload = () => setVersion((v) => v + 1);
  const remove = async (c) => {
    if (!window.confirm("Delete this clue and its files? This cannot be undone.")) return;
    try { await api(`/clues/${id}/${c.id}`, { method: "DELETE" }); reload(); } catch (e) { setErr(e.message); }
  };

  return <>
    {editing && <div className="clue-bar">
      <button type="button" className="btn" onClick={() => setAdding(true)}>+ Add clue</button>
    </div>}
    {err && <p className="err">{err}</p>}
    {!items && !err ? <p className="muted">Loading…</p>
      : items && !items.length ? <p className="muted">{editing ? "No clues yet. Add the first one." : `No clues for ${name} yet.`}</p>
      : items?.map((c) => <ClueCard key={c.id} cc={id} clue={c} editing={editing} onDelete={remove} />)}
    {adding && <ClueEditor cc={id} name={name} onClose={() => setAdding(false)} onSaved={() => { setAdding(false); reload(); }} />}
  </>;
}
