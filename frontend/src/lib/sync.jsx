import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { api, errorText } from "./api.js";
import { loadOptions } from "./filters.js";

export const TYPES = [
  ["daily", "Daily challenges"],
  ["challenge", "Challenges"],
  ["duel", "Duels"],
];

const EMPTY = { saved: 0, found: 0, current: "", errors: [], finished: false };

function reduce(r, ev) {
  switch (ev.type) {
    case "feed": return { ...r, found: r.found + (ev.data.new ?? 0) };
    case "game_started": return { ...r, current: ev.data.map_name || "" };
    case "game_done": return { ...r, saved: r.saved + 1 };
    case "error": return { ...r, errors: [...r.errors, { message: ev.data.message, detail: ev.data.detail || "" }] };
    case "log": return ev.data.level === "error" ? { ...r, errors: [...r.errors, { message: ev.data.message, detail: "" }] } : r;
    case "done": return { ...r, finished: true, current: "" };
    default: return r;
  }
}

const Ctx = createContext(null);

/** One sync job for the whole app, so it keeps running (and reporting) while you change pages. */
export function SyncProvider({ children }) {
  const [types, setTypes] = useState(TYPES.map(([k]) => k));
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState(null);
  const [err, setErr] = useState("");
  const es = useRef(null);
  const subs = useRef(new Set());

  const listen = useCallback(() => {
    es.current?.close();
    setRunning(true);
    setReport(EMPTY);
    const src = (es.current = new EventSource("/api/sync/events"));
    src.onmessage = (m) => {
      const ev = JSON.parse(m.data);
      setReport((r) => reduce(r || EMPTY, ev));
      subs.current.forEach((cb) => cb(ev));
      if (ev.type === "done") {
        src.close();
        setRunning(false);
        loadOptions(true).catch(() => {}); // new game combos may exist now
      }
    };
    src.onerror = () => { src.close(); setRunning(false); setErr("Lost the connection to the sync. Check that the server is running."); };
  }, []);

  useEffect(() => {
    api("/status").then((s) => { if (s.sync_running) listen(); }).catch(() => {});
    return () => es.current?.close();
  }, [listen]);

  const start = useCallback(async () => {
    setErr("");
    try { await api("/sync", { method: "POST", body: { match_types: types } }); listen(); }
    catch (e) { setReport(null); setErr(errorText(e)); }
  }, [types, listen]);

  const dismiss = useCallback(() => { setReport(null); setErr(""); }, []);
  const subscribe = useCallback((cb) => { subs.current.add(cb); return () => subs.current.delete(cb); }, []);

  return <Ctx.Provider value={{ types, setTypes, running, report, err, start, dismiss, subscribe }}>{children}</Ctx.Provider>;
}

export const useSync = () => useContext(Ctx);

/** Run callbacks when a game is saved / the job ends, from any page. */
export function useSyncEvents({ onGameSaved, onFinished }) {
  const { subscribe } = useSync();
  const ref = useRef({});
  ref.current = { onGameSaved, onFinished };
  useEffect(() => subscribe((ev) => {
    if (ev.type === "game_done") ref.current.onGameSaved?.();
    if (ev.type === "done") ref.current.onFinished?.();
  }), [subscribe]);
}
