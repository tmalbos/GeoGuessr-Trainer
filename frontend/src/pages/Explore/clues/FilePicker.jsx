import { useRef, useState } from "react";
import { bboxOf, fitProjection, shapes } from "./geoDraw.js";

const kb = (n) => (n > 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

async function post(url, body, type) {
  const r = await fetch("/api" + url, { method: "POST", body, headers: type ? { "Content-Type": type } : undefined });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

const FolderIcon = () => <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" /></svg>;

// Small "i" that shows `text` on hover or keyboard focus. Not clickable.
const Info = ({ text }) => <span className="info" tabIndex={0} role="img" aria-label={text}>i
  <span className="info-tip" aria-hidden="true">{text}</span></span>;

function GeoThumb({ gj }) {
  const bb = bboxOf(gj);
  if (!bb) return null;
  const W = 260, H = 170;
  const s = shapes(gj, fitProjection(bb, W, H));
  return <svg className="fp-thumb" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Preview of the loaded geometry">
    <path className="cl-poly" d={s.polys} fillRule="evenodd" />
    <path className="cl-line" d={s.lines} fill="none" />
    {s.pts.map(([x, y], i) => <circle key={i} className="cl-pt" cx={x} cy={y} r="2" />)}
  </svg>;
}

function Preview({ kind, v }) {
  let stats = `${kb(v.bytes_in)} → ${kb(v.bytes_out)}`;
  if (kind === "image") stats = `${v.width}×${v.height} · ${stats}`;
  else if (kind === "csv") stats = `${v.row_count} rows · ${stats}`;
  else stats = `${v.kind} · ${v.features.toLocaleString()} features · ${v.vertices_before.toLocaleString()} → ${v.vertices_after.toLocaleString()} vertices · ${stats}`;
  return <div className="fp-prev">
    {kind === "image" && <img className="fp-img" src={`/api/clues/staged/${v.token}/image`} alt="Loaded file preview" />}
    {v.geojson && <GeoThumb gj={v.geojson} />}
    {kind === "csv" && <div className="fp-csv"><table><tbody>
      {v.rows.slice(0, 6).map((r, i) => <tr key={i}>{r.slice(0, 5).map((c, j) => i ? <td key={j}>{c}</td> : <th key={j}>{c}</th>)}</tr>)}
    </tbody></table></div>}
    <div className="muted">{v.name} · {stats}</div>
  </div>;
}

/** Path box + browse button. Also accepts drag and drop, and Ctrl+V of a path, a copied file or an image. */
export default function FilePicker({ kind, label, hint, accept, value, onChange }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const [drag, setDrag] = useState(false);
  const [text, setText] = useState("");
  const attempted = useRef("");
  const input = useRef(null);

  const run = async (job, shown) => {
    attempted.current = shown;
    setText(shown); setBusy(true); setErr("");
    try { onChange(await job()); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const upload = (file) => file && run(() => post(`/clues/stage/${kind}?filename=${encodeURIComponent(file.name)}`, file), file.name);
  const fromPath = (p) => {
    const clean = p.trim().replace(/^"|"$/g, "");
    if (clean) run(() => post(`/clues/stage-path/${kind}`, JSON.stringify({ path: clean }), "application/json"), clean);
  };
  const onPaste = (e) => {
    const file = e.clipboardData.files[0];
    if (file) { e.preventDefault(); upload(file); return; }
    const t = e.clipboardData.getData("text");
    if (t.trim()) { e.preventDefault(); fromPath(t); }
  };

  return <div className={`fp ${drag ? "drag" : ""}`}
    onDragOver={(e) => { e.preventDefault(); setDrag(true); }} onDragLeave={() => setDrag(false)}
    onDrop={(e) => { e.preventDefault(); setDrag(false); upload(e.dataTransfer.files[0]); }}>
    <div className="fp-head"><b>{label}</b>{hint && <Info text={hint} />}</div>
    {value && <Preview kind={kind} v={value} />}
    <div className="fp-row">
      <input value={text} disabled={busy} placeholder="Type a path, or paste a path, file or image" aria-label={`${label} path`}
        onChange={(e) => setText(e.target.value)} onPaste={onPaste}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); fromPath(text); } }}
        onBlur={() => { if (text.trim() && text !== attempted.current && !busy) fromPath(text); }} />
      <button type="button" className="btn ghost fp-browse" disabled={busy} title="Browse files" aria-label={`Browse for ${label}`}
        onClick={() => input.current.click()}><FolderIcon /></button>
      {value && <button type="button" className="btn ghost" onClick={() => { onChange(null); setText(""); attempted.current = ""; }}>Remove</button>}
      <input ref={input} type="file" accept={accept} hidden onChange={(e) => { upload(e.target.files[0]); e.target.value = ""; }} />
    </div>
    {busy && <p className="muted">Processing…</p>}
    {err && <p className="err">{err}</p>}
  </div>;
}
