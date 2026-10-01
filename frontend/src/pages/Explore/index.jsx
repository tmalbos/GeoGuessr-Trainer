import Country from "./Country.jsx";
import WorldMap from "./WorldMap.jsx";
import "./explore.css";

// `country` (ISO code or null) lives in App so the rail can reset it.
export default function Explore({ country, onSelect }) {
  return country
    ? <Country id={country} onBack={() => onSelect(null)} />
    : <WorldMap onSelect={onSelect} />;
}
