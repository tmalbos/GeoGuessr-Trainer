import { useEffect, useState } from "react";
import { clearOrigin, getOrigin, navigate } from "../../lib/router.js";
import Country from "./Country.jsx";
import { loadWorld } from "./geo.js";
import WorldMap from "./WorldMap.jsx";
import "./explore.css";

// The URL decides what is open: #/explore (world), #/explore/AR (country), #/explore/AR/clues (section).
// The query (#/explore/AR/clues?scope=nat&ids=a,b) pre-filters the clue list (see ClueList).
export default function Explore({ seg, query }) {
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
  const originLabel = (o) => {
    const parts = o.split("?")[0].split("/");
    if (parts[1] === "history" && parts[2] === "report") return "Report";
    return { analysis: "Analysis", history: "History", study: "Study", settings: "Settings" }[parts[1]] ?? "Back";
  };
  const back = origin
    ? { label: originLabel(origin), go: () => { clearOrigin(); navigate(origin); } }
    : { label: "World map", go: () => navigate("/explore") };

  if (!code) return <WorldMap onSelect={(c) => navigate(`/explore/${c}`)} />;
  if (!canon) return <p className="muted pad">Loading…</p>;
  return <Country id={canon} section={section} query={query} onSection={(s) => navigate(`/explore/${code}/${s}`)} backLabel={back.label} onBack={back.go} />;
}
