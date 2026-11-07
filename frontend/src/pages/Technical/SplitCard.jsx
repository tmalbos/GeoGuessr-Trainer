import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../../lib/api.js";
import ErrorBox from "../../components/ErrorBox.jsx";

const size = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${Math.max(1, Math.round(n / 1e6))} MB`);
const clock = (s) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
const POLL_MS = 1500;

/** Shown when osmium-tool is not found: how to get it, and a field to point the app at it. */
function OsmiumSetup({ onSaved }) {
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const save = async () => {
    setBusy(true); setErr("");
    try { onSaved(await api("/technical/osm/osmium", { method: "PUT", body: { path } })); }
    catch (e) { setErr(errorText(e)); } finally { setBusy(false); }
  };
  return <div style={{ width: "100%" }}>
    <p>Splitting needs <b>osmium-tool</b>, which isn't set up yet.</p>
    <p className="muted">Download <b>micromamba.exe</b> (one small file), open a terminal where it is and run:<br />
      <code>micromamba create -p tools\osmium -c conda-forge osmium-tool</code><br />
      Run it from the project folder and the app finds <code>tools\osmium</code> by itself.
      Otherwise paste the folder (or the osmium.exe) below. Afterwards micromamba.exe can be deleted.</p>
    <div className="tp-row">
      <input aria-label="osmium path" value={path} placeholder="C:\tools\osmium" onChange={(e) => setPath(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") save(); }} />
      <button type="button" className="btn ghost" disabled={busy || !path.trim()} onClick={save}>Save</button>
      <button type="button" className="btn ghost" disabled={busy} onClick={() => { setPath(""); save(); }}>Check again</button>
    </div>
    {err && <ErrorBox message={err} />}
  </div>;
}

/** Shown when the open file lists several countries: saves one .osm.pbf per country (osmium extract, smart strategy). */
export default function SplitCard({ ds, onOpen }) {
  const [tool, setTool] = useState(null);
  const [job, setJob] = useState(null);
  const [list, setList] = useState([]);
  const [sel, setSel] = useState([]);
  const [folder, setFolder] = useState("");
  const [res, setRes] = useState(null);
  const [err, setErr] = useState(null);

  const apply = useCallback((j) => {
    setJob(j);
    if (j.state === "done" && j.kind === "countries") { setList(j.result.countries); setSel(j.result.countries.map((c) => c.id)); }
    if (j.state === "done" && j.kind === "split") setRes(j.result);
    if (j.state === "error") setErr({ message: j.error, detail: j.detail || "" });
  }, []);

  useEffect(() => { api("/technical/osm/osmium").then(setTool).catch(() => setTool(null)); }, []);

  // New file: reset, and pick up a job that is still running on the server (e.g. after changing pages).
  useEffect(() => {
    let alive = true;
    setJob(null); setList([]); setSel([]); setRes(null); setErr(null);
    api(`/technical/osm/job?token=${ds.token}`).then((j) => { if (alive && j.state !== "idle") apply(j); }).catch(() => {});
    return () => { alive = false; };
  }, [ds.token, apply]);

  const running = job?.state === "running";
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => {
      api(`/technical/osm/job?token=${ds.token}`).then(apply).catch((e) => { setJob(null); setErr({ message: errorText(e), detail: "" }); });
    }, POLL_MS);
    return () => clearInterval(t);
  }, [running, ds.token, apply]);

  const start = async (path, body) => {
    setErr(null); setRes(null);
    try { apply(await api(path, { method: "POST", body: { token: ds.token, ...body } })); }
    catch (e) { setErr({ message: errorText(e), detail: "" }); }
  };

  if (ds.countries.length < 2) return null; // one country in the file: nothing to split

  const missing = tool && !tool.found;
  return <details className="tp-drop">
    <summary><b>Split into one file per country</b> <span className="muted">· {ds.countries.length} {ds.countries.length === 1 ? "country" : "countries"}</span></summary>

    {missing && <OsmiumSetup onSaved={setTool} />}

    {!missing && !list.length && !running && !res &&
      <button type="button" className="btn" onClick={() => start("/technical/osm/countries")}>Find the countries I can split</button>}

    {running && <p className="muted">{job.stage} · {clock(job.elapsed)}</p>}

    {!missing && !!list.length && <>
      <div className="tp-row">
        <button type="button" className="btn ghost" disabled={running} onClick={() => setSel(list.map((c) => c.id))}>All</button>
        <button type="button" className="btn ghost" disabled={running} onClick={() => setSel([])}>None</button>
      </div>
      <div className="tp-tags" style={{ width: "100%" }}>
        {list.map((c) => <label className="tp-tag" key={c.id}>
          <input type="checkbox" disabled={running} checked={sel.includes(c.id)} onChange={() => setSel((s) => (s.includes(c.id) ? s.filter((x) => x !== c.id) : [...s, c.id]))} />
          <span className="tp-dot" style={{ background: "transparent" }} /><span>{c.name}</span><span className="muted">{c.code}</span>
        </label>)}
      </div>
      <label htmlFor="split-folder" className="muted">Save in (empty = next to the file)</label>
      <div className="tp-row">
        <input id="split-folder" value={folder} placeholder="C:\maps\countries" onChange={(e) => setFolder(e.target.value)} />
        <button type="button" className="btn" disabled={running || !sel.length}
          onClick={() => start("/technical/osm/split", { countries: sel, folder })}>
          {running ? "Splitting…" : `Split into ${sel.length} file${sel.length === 1 ? "" : "s"}`}</button>
      </div>
    </>}

    {err && <ErrorBox message={err.message} detail={err.detail} />}
    {res && <div style={{ width: "100%" }}>
      <span className="muted">Saved in {res.folder}</span>
      {res.files.map((f) => <div className="tp-row" key={f.path} style={{ justifyContent: "space-between", marginTop: 6 }}>
        <span title={f.path}>{f.country} <span className="muted">· {size(f.size)}</span></span>
        <button type="button" className="btn ghost" onClick={() => onOpen(f.path)}>Open</button>
      </div>)}
    </div>}
  </details>;
}
