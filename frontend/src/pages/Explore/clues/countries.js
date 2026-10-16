import { useEffect, useMemo, useState } from "react";
import { loadWorld } from "../geo.js";

/** nameOf("CL") -> "Chile"; idOf("CL") -> the id the map files use for it. Falls back to the code until the world loads. */
export function useCountries() {
  const [world, setWorld] = useState(null);
  useEffect(() => {
    let off = false;
    loadWorld().then((w) => !off && setWorld(w)).catch(() => {});
    return () => { off = true; };
  }, []);
  return useMemo(() => {
    const by = new Map((world?.countries ?? []).map((c) => [String(c.id).toUpperCase(), c]));
    return {
      nameOf: (code) => by.get(code.toUpperCase())?.name ?? code.toUpperCase(),
      idOf: (code) => String(by.get(code.toUpperCase())?.id ?? code),
    };
  }, [world]);
}
