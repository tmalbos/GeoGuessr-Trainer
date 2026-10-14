import PlaceExplorer from "./PlaceExplorer.jsx";

// Minigames builds on real map windows cut from OpenStreetMap GeoPackages (see scripts/build_minigame_gpkg.py).
export default function Minigames() {
  return <>
    <h2>Minigames</h2>
    <PlaceExplorer />
  </>;
}
