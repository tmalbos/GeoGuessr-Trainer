import { useEffect, useState } from "react";
import { api, errorText } from "../../lib/api.js";
import ErrorBox from "../../components/ErrorBox.jsx";

const size = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);

/** Shown only when the open file holds several countries: saves one .osm.pbf per country . */
export default function SplitCard({ ds, onOpen }) {
  const [state, setState] = useState("idle");
  const [list, setList] = useState([]);
  const [sel, setSel] = useState([]);
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState(null);
  const [err, setErr] = useState("");

  useEffect(() => {
    let stale = false;
    setState("checking"); setRes(null); setErr("");
    api("/technical/osm/countries", { method: "POST", body: { token: ds.token } })
      .then((r) => { if (!stale) { setList(r.countries); setSel(r.countries.map((c) => c.id)); setState("done"); } })
      .catch(() => { if (!stale) setState("error"); });
    return () => { stale = true; };
  }, [ds.token]);

  const split = async () => {
    setBusy(true); setErr(""); setRes(null);
    try { setRes(await api("/technical/osm/split", { method: "POST", body: { token: ds.token, countries: sel, folder } })); }
    catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };

  if (state === "checking") return <p className="muted">Checking whether the file has several countries…</p>;
  if (state !== "done" || list.length < 2) return null;
  return <details className="tp-drop">
    <summary><b>{list.length} countries in this file</b> <span className="muted">· split into one file each</span></summary>
    <p className="muted">Smaller files open and extract much faster. Roads and boundaries that cross a border are cut at it.</p>
    <div className="tp-row">
      <button type="button" className="btn ghost" onClick={() => setSel(list.map((c) => c.id))}>All</button>
      <button type="button" className="btn ghost" onClick={() => setSel([])}>None</button>
    </div>
    <div className="tp-tags" style={{ width: "100%" }}>
      {list.map((c) => <label className="tp-tag" key={c.id}>
        <input type="checkbox" checked={sel.includes(c.id)} onChange={() => setSel((s) => (s.includes(c.id) ? s.filter((x) => x !== c.id) : [...s, c.id]))} />
        <span className="tp-dot" style={{ background: "transparent" }} /><span>{c.name}</span><span className="muted">{c.code}</span>
      </label>)}
    </div>
    <label htmlFor="split-folder" className="muted">Save in (empty = next to the file)</label>
    <input id="split-folder" value={folder} placeholder="C:\maps\countries" onChange={(e) => setFolder(e.target.value)} />
    <button type="button" className="btn" disabled={busy || !sel.length} onClick={split}>{busy ? "Splitting…" : `Split into ${sel.length} file${sel.length === 1 ? "" : "s"}`}</button>
    {busy && <span className="muted">This reads the whole file once and can take several minutes.</span>}
    {err && <ErrorBox message={err} />}
    {res && <div style={{ width: "100%" }}>
      <span className="muted">Saved in {res.folder}</span>
      {res.files.map((f) => <div className="tp-row" key={f.path} style={{ justifyContent: "space-between", marginTop: 6 }}>
        <span title={f.path}>{f.country} <span className="muted">· {size(f.size)}</span></span>
        <button type="button" className="btn ghost" onClick={() => onOpen(f.path)}>Open</button>
      </div>)}
    </div>}
  </details>;
}
