import { useRef, useState } from "react";
import { api, errorText } from "../lib/api.js";
import { useToast } from "./Toast.jsx";
import "./clue-transfer.css";

/** Export / Import buttons for clue packs (.cluepack), pinned to the top right of the world map.
 *  Export asks which decks to include. Import is immediate and reports what changed. */
export default function ClueTransfer({ onImported }) {
  const toast = useToast();
  const input = useRef(null);
  const [busy, setBusy] = useState(false);
  const [dlg, setDlg] = useState(null); // null | { decks: [{ name, count }], picked: Set }

  const fail = (e) => toast.push(errorText(e), { kind: "err", ms: 6000 });

  const openExport = async () => {
    try {
      const decks = await api("/clue-pack/decks");
      if (!decks.length) { toast.push("There are no clues to export yet."); return; }
      setDlg({ decks, picked: new Set(decks.map((d) => d.name)) });
    } catch (e) { fail(e); }
  };

  const toggle = (name) => setDlg((d) => {
    const picked = new Set(d.picked);
    if (!picked.delete(name)) picked.add(name);
    return { ...d, picked };
  });

  const doExport = async () => {
    setBusy(true);
    try {
      const r = await fetch("/api/clue-pack/export", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decks: [...dlg.picked] }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement("a");
      a.href = url;
      a.download = `clues-${new Date().toISOString().slice(0, 10)}.cluepack`;
      a.click();
      URL.revokeObjectURL(url);
      setDlg(null);
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  const doImport = async (file) => {
    if (!file) return;
    setBusy(true);
    try {
      const r = await fetch("/api/clue-pack/import", { method: "POST", body: file });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : r.statusText);
      toast.push(`${body.added} added, ${body.updated} updated${body.skipped ? `, ${body.skipped} skipped` : ""}`, { kind: "ok" });
      onImported?.();
    } catch (e) { fail(e); } finally { setBusy(false); }
  };

  return <>
    <div className="ct">
      <button type="button" className="btn ghost" disabled={busy} onClick={openExport}>Export clues</button>
      <button type="button" className="btn ghost" disabled={busy} onClick={() => input.current.click()}>Import clues</button>
      <input ref={input} type="file" accept=".cluepack" hidden onChange={(e) => { doImport(e.target.files[0]); e.target.value = ""; }} />
    </div>
    {dlg && <div className="confirm-back" onMouseDown={(e) => { if (e.target === e.currentTarget) setDlg(null); }}>
      <div className="confirm" role="dialog" aria-modal="true" aria-label="Export clues">
        <h3>Export clues</h3>
        <p className="muted">Pick the decks to include.</p>
        {dlg.decks.map((d) => <label className="ct-opt" key={d.name}>
          <input type="checkbox" checked={dlg.picked.has(d.name)} onChange={() => toggle(d.name)} />
          {d.name}<span className="muted">{d.count} clue{d.count === 1 ? "" : "s"}</span>
        </label>)}
        <div className="confirm-actions">
          <button type="button" className="btn ghost" onClick={() => setDlg(null)}>Cancel</button>
          <button type="button" className="btn" disabled={busy || !dlg.picked.size} onClick={doExport}>{busy ? "Exporting…" : "Export"}</button>
        </div>
      </div>
    </div>}
  </>;
}
