import { useState } from "react";
import { errorText } from "../lib/api.js";
import { mapConfig, providerOptions, saveMapSettings } from "../lib/mapProviders.js";

/** Settings card: where map tiles (and Street View photos) come from. Saving reloads the page. */
export default function MapSourceCard() {
  const cfg = mapConfig();
  const options = providerOptions();
  const [provider, setProvider] = useState(options.some((o) => o.id === cfg.provider) ? cfg.provider : "osm");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const save = async (body) => {
    setBusy(true); setErr("");
    try {
      await saveMapSettings(body);
      window.location.reload();
    } catch (e) { setErr(errorText(e)); setBusy(false); }
  };

  return <div className="card">
    <b>Map source</b>
    <label htmlFor="map-source">Tiles and Street View photos</label>
    <select id="map-source" value={provider} onChange={(e) => setProvider(e.target.value)}>
      {options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}
    </select>
    {provider === "osm" && <p className="muted">Free and keyless, meant for light use. Street View photos are not available with this source.</p>}
    {provider === "google" && <>
      <label htmlFor="map-key">Google API key{cfg.google_key ? " (one is saved)" : ""}</label>
      <input id="map-key" type="password" autoComplete="off" value={key} placeholder={cfg.google_key ? "Leave empty to keep it" : "Paste your key"}
        onChange={(e) => setKey(e.target.value)} />
      <p className="muted">Needs a Google Cloud key with the Map Tiles API and the Street View Static API enabled. Usage is billed to your account by Google.</p>
    </>}
    {cfg.provider === "google" && cfg.error && <p className="err">{cfg.error}</p>}
    {err && <p className="err">{err}</p>}
    <p style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
      <button type="button" className="btn" disabled={busy} onClick={() => save({ provider, google_key: key.trim() || undefined })}>Save</button>
      {cfg.google_key && <button type="button" className="btn ghost" disabled={busy}
        onClick={() => save({ provider: provider === "google" ? "osm" : provider, google_key: "" })}>Remove saved key</button>}
    </p>
  </div>;
}
