import { useCallback, useEffect, useState } from "react";
import ErrorBox from "../components/ErrorBox.jsx";
import MapSourceCard from "../components/MapSourceCard.jsx";
import { Skeleton } from "../components/Skeleton.jsx";
import StatusGrid from "../components/StatusGrid.jsx";
import { useToast } from "../components/Toast.jsx";
import { api, errorText } from "../lib/api.js";

export default function Settings() {
  const toast = useToast();
  const [saved, setSaved] = useState(null);
  const [draft, setDraft] = useState({ cookie: "", userId: "", lang: "en", mergeShortcut: "Ctrl+G" });
  const [loadErr, setLoadErr] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(() => {
    setLoadErr("");
    return api("/settings")
      .then((s) => { setSaved(s); setDraft({ cookie: "", userId: s.user_id, lang: s.lang, mergeShortcut: s.merge_shortcut }); })
      .catch((e) => setLoadErr(errorText(e)));
  }, []);
  useEffect(() => { load(); }, [load]);

  const set = (k) => (e) => setDraft((d) => ({ ...d, [k]: e.target.value }));
  const idEmpty = !draft.userId.trim();
  const mergeEmpty = !draft.mergeShortcut.trim();
  const dirty = !!saved && (draft.cookie.trim() !== "" || draft.userId.trim() !== saved.user_id || draft.lang !== saved.lang
    || draft.mergeShortcut.trim() !== saved.merge_shortcut);

  const save = async () => {
    setSaving(true);
    try {
      if (draft.cookie.trim()) await api("/settings/cookie", { method: "PUT", body: { cookie: draft.cookie } });
      if (draft.userId.trim() !== saved.user_id) await api("/settings/user", { method: "PUT", body: { user_id: draft.userId } });
      if (draft.lang !== saved.lang) await api("/settings/lang", { method: "PUT", body: { lang: draft.lang } });
      if (draft.mergeShortcut.trim() !== saved.merge_shortcut) await api("/settings/merge-shortcut", { method: "PUT", body: { shortcut: draft.mergeShortcut.trim() } });
      toast.push("Settings saved", { kind: "ok" });
      await load();
    } catch (e) {
      toast.push(errorText(e), { kind: "err", ms: 6000 });
    } finally { setSaving(false); }
  };

  const refreshCookie = async () => {
    try { await api("/settings/cookie/refresh", { method: "POST" }); toast.push("Cookie refreshed from your browser", { kind: "ok" }); load(); }
    catch (e) { toast.push(errorText(e), { kind: "err", ms: 6000 }); }
  };

  return <><h2>Settings</h2>
    <h3>System status</h3>
    <StatusGrid />
    <h3>Configuration</h3>
    {loadErr ? <ErrorBox message={loadErr} onRetry={load} /> : !saved ? <div className="skel-rows"><Skeleton h={40} r={8} /><Skeleton h={40} r={8} /><Skeleton h={40} r={8} /></div> : <>
      <label htmlFor="ck">GeoGuessr cookie (_ncfa) {saved.cookie && <span className="muted">— saved</span>}</label>
      <input id="ck" type="password" value={draft.cookie} onChange={set("cookie")} placeholder={saved.cookie ? "Paste a new value to replace it" : "Paste cookie value"} />
      <p><button type="button" className="btn ghost" onClick={refreshCookie}>Get it from my browser</button></p>
      <label htmlFor="uid">GeoGuessr user ID</label>
      <input id="uid" value={draft.userId} onChange={set("userId")} aria-invalid={idEmpty} />
      {idEmpty && <p className="err">The user ID can't be empty.</p>}
      <label htmlFor="lang">Language</label>
      <select id="lang" value={draft.lang} onChange={set("lang")}>
        <option value="en">English</option><option value="es">Español</option></select>
      <label htmlFor="merge-shortcut">Merge shortcut (OSM extractor)</label>
      <input id="merge-shortcut" value={draft.mergeShortcut} onChange={set("mergeShortcut")} placeholder="Ctrl+G" aria-invalid={mergeEmpty} />
      {mergeEmpty && <p className="err">The shortcut can't be empty.</p>}
      <p style={{ display: "flex", gap: 10, marginTop: 20 }}>
        <button type="button" className="btn" disabled={!dirty || idEmpty || mergeEmpty || saving} onClick={save}>{saving ? "Saving…" : "Save changes"}</button>
        {dirty && <button type="button" className="btn ghost" disabled={saving} onClick={() => setDraft({ cookie: "", userId: saved.user_id, lang: saved.lang, mergeShortcut: saved.merge_shortcut })}>Discard</button>}
      </p>
    </>}
    <h3>Maps</h3>
    <MapSourceCard />
  </>;
}
