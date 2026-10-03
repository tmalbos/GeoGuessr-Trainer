import { useCallback, useEffect, useState } from "react";
import { api, errorText } from "../lib/api.js";
import ErrorBox from "./ErrorBox.jsx";
import { SkeletonCard } from "./Skeleton.jsx";
import StatusIcon from "./StatusIcon.jsx";

export default function StatusGrid() {
  const [{ data, error }, setState] = useState({ data: null, error: "" });
  const load = useCallback(() =>
    api("/status").then((d) => setState({ data: d, error: "" })).catch((e) => setState((s) => ({ ...s, error: errorText(e) }))), []);
  useEffect(() => { load(); const t = setInterval(load, 5000); return () => clearInterval(t); }, [load]);

  if (error) return <ErrorBox message={error} onRetry={load} />;
  if (!data) return <div className="grid">{[0, 1, 2, 3].map((i) => <SkeletonCard key={i} lines={1} />)}</div>;

  const geo = { ready: "ok", loading: "wait", error: "bad" }[data.geodata];
  const geoMsg = { ready: "Geo layers loaded", loading: "Loading geo layers…", error: "Geo layers failed to load. Check data/geo/." }[data.geodata];
  const items = [
    ["Database", data.db ? "ok" : "bad", data.db ? "Connected" : "Unavailable. Check Postgres and PG_DSN."],
    ["Anki", data.anki ? "ok" : "warn", data.anki ? "AnkiConnect reachable" : "Optional. Open Anki to create cards."],
    ["Geodata", geo, geoMsg],
    ["GeoGuessr cookie", data.cookie ? "ok" : "bad", data.cookie ? "Cookie saved" : "Paste one below."],
  ];
  return <div className="grid">{items.map(([t, k, m]) =>
    <div className="card" key={t}><b><StatusIcon kind={k} />{t}</b><div className="muted">{m}</div></div>)}</div>;
}
