import { useEffect, useMemo, useState } from "react";

// Hash routing (#/explore/AR?x=1): works with any static server, no backend fallback needed.
const current = () => window.location.hash.replace(/^#/, "");
const decode = (s) => { try { return decodeURIComponent(s); } catch { return s; } };

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
