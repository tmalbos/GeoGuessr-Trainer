import { useState } from "react";
import PlaceExplorer from "./PlaceExplorer.jsx";
import StreetView from "./StreetView.jsx";

// Minigames builds on real map windows cut from OpenStreetMap GeoPackages (see scripts/build_minigame_gpkg.py).
const TABS = [["street", "Street view (3D)"], ["map", "Map explorer"]];

export default function Minigames() {
  const [tab, setTab] = useState("street");
  return <>
    <h2>Minigames</h2>
    <div className="tabs" role="tablist">
      {TABS.map(([k, label]) => <button key={k} type="button" role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{label}</button>)}
    </div>
    {tab === "street" ? <StreetView /> : <PlaceExplorer />}
  </>;
}
