import { useEffect, useMemo, useRef, useState } from "react";
import ErrorBox from "../../components/ErrorBox.jsx";

const MAX_ADM = 5; // no country has more official levels than this

/** OSM admin_level -> suggested ADM number. Walking from the coarsest level, a level gets the next ADM number only
 *  when it has more polygons than any level before it; otherwise it shares the previous number. Capped at MAX_ADM. */
function suggestAdm(levels) {
  const out = {};
  let biggest = 0;
  let adm = -1;
  for (const { level, count } of [...levels].sort((a, b) => a.level - b.level)) {
    if (count > biggest) { biggest = count; adm = Math.min(adm + 1, MAX_ADM); }
    out[level] = Math.max(adm, 0);
  }
  return out;
}

/** Save dialog for the admin TopoJSON: file name, ADM number, where the coarser names come from, simplification.
 *  levels = [{ level, count }] of the country; selected = ticked OSM levels. onExport(settings) must throw on failure. */
export default function ExportDialog({ ds, levels, selected, onClose, onExport }) {
  const suggested = useMemo(() => suggestAdm(levels), [levels]);
  const [adm, setAdm] = useState(() => Math.max(0, ...selected.map((l) => suggested[l] ?? 0)));
  const [name, setName] = useState(null); // null = follow the suggestion (ADMx)
  const [simplify, setSimplify] = useState(0);
  const [chosen, setChosen] = useState({}); // ADM number -> "L:<osm level>" | "F:<file id>" | ""
  const [files, setFiles] = useState([]);
  const [uploading, setUploading] = useState(0);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const input = useRef(null);
  const target = useRef(0);

  useEffect(() => {
    const onKey = (e) => { if (e.key === "Escape" && !busy) { e.stopPropagation(); e.preventDefault(); onClose(); } };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, [busy, onClose]);

  // Default source for ADM k: the OSM level suggested as ADM k, the one with the most polygons.
  const best = (k) => {
    let top = null;
    for (const l of levels) if (suggested[l.level] === k && (!top || l.count > top.count)) top = l;
    return top ? `L:${top.level}` : "";
  };
  const sourceOf = (k) => chosen[k] ?? best(k);
  const ks = Array.from({ length: adm }, (_, i) => i + 1).filter((k) => k < adm);

  const importFile = async (k, file) => {
    if (!file) return;
    setErr(""); setUploading(k);
    try {
      const r = await fetch(`/api/technical/pbf/parent-file?token=${ds.token}&filename=${encodeURIComponent(file.name)}`, { method: "POST", body: file });
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
      const f = await r.json();
      setFiles((l) => [...l, f]);
      setChosen((c) => ({ ...c, [k]: `F:${f.id}` }));
    } catch (e) { setErr(e.message); } finally { setUploading(0); }
  };

  const submit = async () => {
    setBusy(true); setErr("");
    const parents = ks.map((k) => {
      const v = sourceOf(k);
      if (v.startsWith("L:")) return { adm: k, level: Number(v.slice(2)) };
      if (v.startsWith("F:")) return { adm: k, file: v.slice(2) };
      return null;
    }).filter(Boolean);
    try { await onExport({ name: name ?? `ADM${adm}`, adm, simplify, parents }); }
    catch (e) { setErr(e.message); setBusy(false); }
  };

  return <div className="confirm-back" onMouseDown={(e) => { if (e.target === e.currentTarget && !busy) onClose(); }}>
    <div className="confirm" role="dialog" aria-modal="true" aria-label="Export TopoJSON" style={{ width: "min(520px, 100%)", maxHeight: "90vh", overflowY: "auto" }}>
      <h3>Export TopoJSON</h3>

      <div className="tp-field">
        <label htmlFor="ex-name">File name</label>
        <span className="tp-suffix">
          <input id="ex-name" value={name ?? `ADM${adm}`} onChange={(e) => setName(e.target.value)} />
          <span className="muted">.topojson</span>
        </span>
      </div>

      <div className="tp-field">
        <label htmlFor="ex-simplify">Simplification <span className="muted">{simplify ? `${simplify}%` : "Off"}</span></label>
        <input id="ex-simplify" type="range" min="0" max="100" step="1" value={simplify} onChange={(e) => setSimplify(Number(e.target.value))} />
      </div>

      <hr className="tp-divider" />

      <div className="tp-field">
        <label htmlFor="ex-adm">Admin level <span className="muted">{selected.map((l) => `Level ${l} → ADM${suggested[l] ?? 0}`).join(", ")} (suggested)</span></label>
        <select id="ex-adm" value={adm} onChange={(e) => setAdm(Number(e.target.value))}>
          {Array.from({ length: MAX_ADM + 1 }, (_, k) => <option key={k} value={k}>ADM{k}</option>)}
        </select>
      </div>

      {ks.map((k) => <div className="tp-field" key={k}>
        <label htmlFor={`ex-parent-${k}`}>SUBDIVISION_NAME_{k} comes from</label>
        <div className="tp-row">
          <select id={`ex-parent-${k}`} value={sourceOf(k)} onChange={(e) => setChosen((c) => ({ ...c, [k]: e.target.value }))}>
            <option value="">Nothing (leave empty)</option>
            {levels.map((l) => <option key={l.level} value={`L:${l.level}`}>Level {l.level} · {l.count.toLocaleString()} (ADM{suggested[l.level]})</option>)}
            {files.map((f) => <option key={f.id} value={`F:${f.id}`}>{f.name} · {f.count.toLocaleString()}</option>)}
          </select>
          <button type="button" className="btn ghost" disabled={uploading === k}
            onClick={() => { target.current = k; input.current?.click(); }}>{uploading === k ? "Reading…" : "Import"}</button>
        </div>
      </div>)}
      <input ref={input} type="file" accept=".json,.geojson,.topojson" hidden
        onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; importFile(target.current, f); }} />

      {err && <ErrorBox message={err} />}
      <div className="confirm-actions">
        <button type="button" className="btn ghost" disabled={busy} onClick={onClose}>Cancel</button>
        <button type="button" className="btn" disabled={busy || uploading > 0 || !(name ?? "x").trim()} onClick={submit}>{busy ? "Building…" : "Export"}</button>
      </div>
    </div>
  </div>;
}
