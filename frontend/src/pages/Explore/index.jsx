import { useEffect, useState } from "react";
import { clearOrigin, getOrigin, navigate } from "../../lib/router.js";
import Country from "./Country.jsx";
import { loadWorld } from "./geo.js";
import WorldMap from "./WorldMap.jsx";
import "./explore.css";

// The URL decides what is open: #/explore (world), #/explore/AR (country), #/explore/AR/clues (section).
export default function Explore({ seg }) {
  const [code, section] = seg;
  const [canon, setCanon] = useState(null);

  // Links may use "ES" while the map files use another case: resolve to the id the map data uses.
  useEffect(() => {
    if (!code) return undefined;
    let off = false;
    setCanon(null);
    loadWorld()
      .then((w) => { if (!off) setCanon(String(w.countries.find((c) => String(c.id).toUpperCase() === code.toUpperCase())?.id ?? code)); })
      .catch(() => { if (!off) setCanon(code); });
    return () => { off = true; };
  }, [code]);

  // Came here from another page (e.g. Analysis)? Then "back" goes there, not to the world map.
  const origin = getOrigin();
  const back = origin
    ? { label: { analysis: "Analysis", history: "History", study: "Study", settings: "Settings" }[origin.split("?")[0].split("/")[1]] ?? "Back",
        go: () => { clearOrigin(); navigate(origin); } }
    : { label: "World map", go: () => navigate("/explore") };

  if (!code) return <WorldMap onSelect={(c) => navigate(`/explore/${c}`)} />;
  if (!canon) return <p className="muted pad">Loading…</p>;
  return <Country id={canon} section={section} onSection={(s) => navigate(`/explore/${code}/${s}`)} backLabel={back.label} onBack={back.go} />;
}
