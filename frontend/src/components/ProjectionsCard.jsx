import { useEffect, useMemo, useState } from "react";
import { api, errorText } from "../lib/api.js";
import { PROJECTIONS } from "../pages/Explore/clues/projections.js";
import { loadWorld } from "../pages/Explore/geo.js";
import { useToast } from "./Toast.jsx";

const LABEL = Object.fromEntries(PROJECTIONS);

/** Settings card: Country -> map projection rules. Countries without a rule use Standard.
 *  Saving a changed rule redraws that country's saved regional maps (and resets their locked zoom). */
export default function ProjectionsCard() {
  const toast = useToast();
  const [rules, setRules] = useState(null);
  const [world, setWorld] = useState(null);
  const [code, setCode] = useState("");
  const [kind, setKind] = useState(PROJECTIONS[1][0]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let off = false;
    api("/settings").then((s) => !off && setRules(s.country_projections ?? {}))
      .catch((e) => toast.push(errorText(e), { kind: "err", ms: 6000 }));
    loadWorld().then((w) => !off && setWorld(w)).catch(() => {});
    return () => { off = true; };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const countries = useMemo(() => (world?.countries ?? [])
    .map((c) => ({ code: String(c.id).toUpperCase(), name: c.name }))
    .sort((a, b) => a.name.localeCompare(b.name)), [world]);
  const nameOf = (cc) => countries.find((c) => c.code === cc)?.name ?? cc;

  const save = async (next) => {
    setBusy(true);
    try {
      const r = await api("/settings/projections", { method: "PUT", body: { rules: next } });
      setRules(r.rules);
      toast.push(r.rerendered ? `Saved. ${r.rerendered} map${r.rerendered === 1 ? "" : "s"} redrawn.` : "Saved", { kind: "ok" });
      return true;
    } catch (e) {
      toast.push(errorText(e), { kind: "err", ms: 6000 });
      return false;
    } finally { setBusy(false); }
  };
  const add = async () => { if (code && await save({ ...rules, [code]: kind })) setCode(""); };
  const remove = (cc) => { const { [cc]: _gone, ...rest } = rules; save(rest); };

  return <div className="card">
    <b>Map projections</b>
    <p className="muted">How the regional clue maps of a country are drawn. Countries without a rule use Standard.
      Changing a rule redraws that country's saved maps and moves their locked zoom to match.</p>
    {rules && Object.entries(rules).sort().map(([cc, k]) => <div key={cc} style={{ display: "flex", alignItems: "center", gap: 10, margin: "6px 0" }}>
      <span style={{ flex: 1 }}>{nameOf(cc)}</span>
      <span className="chip" style={{ "--h": 215 }}>{LABEL[k] ?? k}</span>
      <button type="button" className="link-btn" disabled={busy} onClick={() => remove(cc)}>Remove</button>
    </div>)}
    <div style={{ display: "flex", flexWrap: "wrap", gap: 10, marginTop: 10 }}>
      <select aria-label="Country" value={code} onChange={(e) => setCode(e.target.value)}>
        <option value="">Country…</option>
        {countries.map((c) => <option key={c.code} value={c.code}>{c.name}</option>)}
      </select>
      <select aria-label="Projection" value={kind} onChange={(e) => setKind(e.target.value)}>
        {PROJECTIONS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
      </select>
      <button type="button" className="btn" disabled={!rules || !code || busy} onClick={add}>{busy ? "Saving…" : "Add"}</button>
    </div>
  </div>;
}
