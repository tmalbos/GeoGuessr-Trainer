import { useEffect, useMemo, useState } from "react";

// Hash routing (#/explore/AR?x=1): works with any static server, no backend fallback needed.
const current = () => window.location.hash.replace(/^#/, "");
const decode = (s) => { try { return decodeURIComponent(s); } catch { return s; } };
const pageOf = (hash) => hash.split("?")[0].split("/").filter(Boolean)[0] ?? "";
const depthOf = (hash) => hash.split("?")[0].split("/").filter(Boolean).length;

// Where the user was before entering Explore from another page (e.g. "/analysis/country?q=Spain").
// The country page uses it so its back button returns there instead of to the world map.
let origin = null;
export const getOrigin = () => origin;
export const clearOrigin = () => { origin = null; };

window.addEventListener("hashchange", (e) => {
  const next = current();
  const prev = (e.oldURL.split("#")[1] ?? "");
  if (pageOf(next) !== "explore" || depthOf(next) < 2) origin = null; // left Explore, or back on the world map
  else if (pageOf(prev) !== "explore") origin = prev || null; // just arrived from another page
});

export function useHashRoute() {
  const [raw, setRaw] = useState(current);
  useEffect(() => {
    const on = () => setRaw(current());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return useMemo(() => {
    const [path, qs = ""] = raw.split("?");
    return { segments: path.split("/").filter(Boolean).map(decode), query: new URLSearchParams(qs) };
  }, [raw]);
}

export const navigate = (path) => { window.location.hash = path.startsWith("/") ? path : `/${path}`; };
