import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useToast } from "../../../components/Toast.jsx";
import { api, errorText } from "../../../lib/api.js";
import fileUrl from "./fileUrl.js";
import RichText from "./richText.jsx";
import "./anki-export.css";

const Icon = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
  strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="4" y="3" width="16" height="18" rx="3" />
  <path d="M12 8l1.2 2.8 3 .3-2.3 2 .7 3-2.6-1.6-2.6 1.6.7-3-2.3-2 3-.3z" fill="currentColor" stroke="none" /></svg>;

/** Square button (top right of a national clue) + the popup that sends the clue to Anki through AnkiConnect. */
export default function AnkiExport({ cc, clue }) {
  const toast = useToast();
  const [open, setOpen] = useState(false);
  const [invert, setInvert] = useState(false);
  const [card, setCard] = useState(null);
  const [decks, setDecks] = useState(null);
  const [deck, setDeck] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return undefined;
    let off = false;
    setErr(""); setDecks(null);
    api("/anki/decks").then((d) => { if (!off) { setDecks(d.decks); setDeck(d.default); } })
      .catch((e) => !off && setErr(errorText(e)));
    const onKey = (e) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("keydown", onKey);
    return () => { off = true; document.removeEventListener("keydown", onKey); };
  }, [open]);

  useEffect(() => {
    if (!open) return undefined;
    let off = false;
    setCard(null);
    api(`/anki/preview/${cc}/${clue.id}?invert=${invert}`).then((c) => !off && setCard(c)).catch((e) => !off && setErr(errorText(e)));
    return () => { off = true; };
  }, [open, invert, cc, clue.id]);

  const send = async () => {
    setBusy(true); setErr("");
    try {
      await api("/anki/export", { method: "POST", body: { cc, clue_id: clue.id, invert, deck } });
      toast.push("Added to Anki", { kind: "ok" });
      setOpen(false);
    } catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };

  return <>
    <button type="button" className="anki-btn" aria-label="Add to Anki" title="Add to Anki" onClick={() => setOpen(true)}><Icon /></button>
    {open && createPortal(<div className="confirm-back" onMouseDown={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
      <div className="confirm anki-dlg" role="dialog" aria-modal="true" aria-label="Add to Anki">
        <h3>Add to Anki</h3>
        <label htmlFor="anki-deck">Deck</label>
        <select id="anki-deck" value={deck} disabled={!decks} onChange={(e) => setDeck(e.target.value)}>
          {(decks ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
        </select>
        <label className="anki-inv"><input type="checkbox" checked={invert} onChange={(e) => setInvert(e.target.checked)} /> Invert</label>
        {card && <div className="anki-prev">
          <p><b>{card.question}</b></p>
          {card.image && <img src={fileUrl(card.cc, card.image)} alt="" />}
          <hr />
          <p><RichText text={card.answer} /></p>
        </div>}
        {err && <p className="err">{err}</p>}
        <div className="confirm-actions">
          <button type="button" className="btn ghost" onClick={() => setOpen(false)}>Cancel</button>
          <button type="button" className="btn" disabled={busy || !card || !decks} onClick={send}>{busy ? "Adding…" : "Add"}</button>
        </div>
      </div>
    </div>, document.body)}
  </>;
}
