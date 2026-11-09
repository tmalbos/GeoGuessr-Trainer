import { useEffect, useState } from "react";
import { api, errorText } from "../lib/api.js";
import { useToast } from "./Toast.jsx";

// Same rule as acronym() in src/shared/decks.py: first letter of each word.
const acronym = (n) => (n.match(/[\p{L}\p{N}_]+/gu) ?? []).map((w) => w[0]).join("").toUpperCase() || "X";

/** Settings card: the decks (groups) clues can belong to. Changes are saved as they are made. */
export default function DecksCard() {
  const toast = useToast();
  const [decks, setDecks] = useState(null);
  const [name, setName] = useState("");

  useEffect(() => {
    api("/settings").then((s) => setDecks(s.decks)).catch((e) => toast.push(errorText(e), { kind: "err", ms: 6000 }));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async (next) => {
    try { setDecks((await api("/settings/decks", { method: "PUT", body: { decks: next } })).decks); return true; }
    catch (e) { toast.push(errorText(e), { kind: "err", ms: 6000 }); return false; }
  };
  const add = async () => { if (name.trim() && await save([...decks, name.trim()])) setName(""); };

  return <div className="card">
    <b>Clue decks</b>
    <p className="muted">Every clue belongs to a deck, and its id starts with the deck's initials. Decks are what you pick when exporting.</p>
    {decks?.map((d) => <div key={d} style={{ display: "flex", alignItems: "center", gap: 10, margin: "6px 0" }}>
      <span className="chip" style={{ "--h": 215 }}>{acronym(d)}</span>
      <span style={{ flex: 1 }}>{d}</span>
      <button type="button" className="link-btn" disabled={decks.length < 2} onClick={() => save(decks.filter((x) => x !== d))}>Remove</button>
    </div>)}
    <div style={{ display: "flex", gap: 10, marginTop: 10 }}>
      <input value={name} placeholder="New deck name" aria-label="New deck name" onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); add(); } }} />
      <button type="button" className="btn" disabled={!decks || !name.trim()} onClick={add}>Add</button>
    </div>
  </div>;
}
