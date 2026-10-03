import { useState } from "react";
import { useSync } from "../lib/sync.jsx";
import StatusIcon from "./StatusIcon.jsx";
import "./sync-button.css";

/** Sync status, visible on every page: progress while running, then the result or the error. */
export default function SyncChip() {
  const { running, report, err, dismiss } = useSync();
  const [details, setDetails] = useState(false);
  if (!running && !report && !err) return null;

  const error = err ? { message: err, detail: "" } : !running ? report?.errors[0] : null;
  const kind = running ? "run" : error ? "err" : "ok";
  let text;
  if (running) text = report?.found ? `Syncing ${report.saved} of ${report.found}` : "Syncing…";
  else if (error) text = error.message;
  else text = report?.saved ? `Synced ${report.saved} new game${report.saved === 1 ? "" : "s"}` : "Already up to date";

  return <div className={`sync-chip ${kind}`} role="status">
    <div className="sync-chip-row">
      {running ? <span className="split-icon spin" aria-hidden="true">↻</span> : <StatusIcon kind={kind === "err" ? "bad" : "ok"} />}
      <span className="sync-chip-text">{text}{running && report?.current && <span className="muted"> · {report.current}</span>}</span>
      {!running && <button type="button" aria-label="Dismiss" onClick={() => { dismiss(); setDetails(false); }}>×</button>}
    </div>
    {running && report?.found > 0 && <div className="bar"><i style={{ width: `${Math.min(100, (report.saved / report.found) * 100)}%` }} /></div>}
    {error?.detail && <>
      <button type="button" className="link-btn" onClick={() => setDetails((d) => !d)}>{details ? "Hide details" : "Details"}</button>
      {details && <pre>{error.detail}</pre>}
    </>}
  </div>;
}
