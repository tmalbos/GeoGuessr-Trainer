import { useEffect, useState } from "react";
import { api } from "../lib/api.js";

export default function Dashboard() {
  const [s, setS] = useState(null);
  useEffect(() => {
    const load = () => api("/status").then(setS).catch(() => setS(null));
    load(); const t = setInterval(load, 5000); return () => clearInterval(t);
  }, []);
  const items = s && [
    ["Database", s.db ? "ok" : "bad", s.db ? "Connected" : "Unavailable. Check Postgres and PG_DSN."],
    ["Anki", s.anki ? "ok" : "bad", s.anki ? "AnkiConnect reachable" : "Open Anki with AnkiConnect."],
    ["Ecoregions", { ready: "ok", loading: "wait", error: "bad" }[s.ecoregions], { ready: "Shapefile loaded", loading: "Loading shapefile…", error: "Shapefile failed to load" }[s.ecoregions]],
    ["GeoGuessr cookie", s.cookie ? "ok" : "bad", s.cookie ? "Cookie saved" : "Add one in Settings."],
  ];
  return <><h2>Dashboard</h2>{!s ? <p className="err">Backend unreachable. Is uvicorn running?</p> :
    <div className="grid">{items.map(([t, k, m]) => <div className="card" key={t}><b><span className={`dot ${k}`} />{t}</b><div className="muted">{m}</div></div>)}</div>}</>;
}
