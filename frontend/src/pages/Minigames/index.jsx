import StreetGenerator from "./StreetGenerator.jsx";

// For now Minigames only hosts the street network generator (the base both minigames will build on).
export default function Minigames() {
  return <>
    <h2>Minigames</h2>
    <StreetGenerator />
  </>;
}
