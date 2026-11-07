import { useRef, useState } from "react";
import StatusIcon from "../../components/StatusIcon.jsx";

export default function FileDropPanel({ title, accept, upload, loading, value = "", placeholder }) {
  const [over, setOver] = useState(false);
  const pick = useRef(null);

  return <div className={`tp-drop ${over ? "over" : ""}`}
    onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
    onDrop={(e) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); }}>
    <h3>{title}</h3>
    <div className="tp-row">
      <input value={value} placeholder={placeholder} readOnly
        onClick={() => { if (!loading) pick.current?.click(); }}
        onKeyDown={(e) => { if (e.key === "Enter" && !loading) pick.current?.click(); }} />
      <button type="button" className="btn ghost" disabled={loading}
        onClick={() => pick.current?.click()}>{loading ? "Opening…" : "Open"}</button>
      <input ref={pick} type="file" accept={accept} hidden onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; upload(f); }} />
    </div>
    {loading && <span className="muted" role="status"><StatusIcon kind="wait" />Opening the file. A big file can take a few minutes the first time.</span>}
  </div>;
}
