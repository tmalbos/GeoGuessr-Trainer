import { useEffect, useState } from "react";
import { api } from "../lib/api.js";

export default function Settings() {
  const [s, setS] = useState(null);
  const [cookie, setCookie] = useState("");
  const [msg, setMsg] = useState("");
  useEffect(() => { api("/settings").then(setS); }, []);
  const run = async (fn, ok) => { try { await fn(); setMsg(ok); api("/settings").then(setS); } catch (e) { setMsg(e.message); } };
  if (!s) return null;
  return <><h2>Settings</h2>
    <label htmlFor="ck">GeoGuessr cookie (_ncfa) {s.cookie && <span className="muted">— saved</span>}</label>
    <input id="ck" type="password" value={cookie} onChange={(e) => setCookie(e.target.value)} placeholder="Paste cookie value" />
    <p><button className="btn" disabled={!cookie} onClick={() => run(() => api("/settings/cookie", { method: "PUT", body: { cookie } }), "Cookie saved").then(() => setCookie(""))}>Save cookie</button>{" "}
      <button className="btn ghost" onClick={() => run(() => api("/settings/cookie/refresh", { method: "POST" }), "Cookie refreshed from browser")}>Refresh from browser</button></p>
    <label htmlFor="uid">GeoGuessr user ID</label>
    <input id="uid" defaultValue={s.user_id} onBlur={(e) => run(() => api("/settings/user", { method: "PUT", body: { user_id: e.target.value } }), "User ID saved")} />
    <label htmlFor="lang">Language</label>
    <select id="lang" value={s.lang} onChange={(e) => run(() => api("/settings/lang", { method: "PUT", body: { lang: e.target.value } }), "Language changed")}>
      <option value="en">English</option><option value="es">Español</option></select>
    {msg && <p className="muted">{msg}</p>}</>;
}
