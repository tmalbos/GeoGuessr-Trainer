import { useState } from "react";
import Analysis from "./pages/Analysis/index.jsx";
import Explore from "./pages/Explore/index.jsx";
import History from "./pages/History.jsx";
import Settings from "./pages/Settings.jsx";
import Study from "./pages/Study/index.jsx";

const PAGES = { Explore, History, Analysis, Study, Settings };

export default function App() {
  const [page, setPage] = useState("Explore");
  const [country, setCountry] = useState(null); // ISO code of the open country page, or null = world map
  const Page = PAGES[page];
  // Clicking "Explore" while already on Explore goes back to the world map.
  const go = (p) => { if (p === "Explore" && page === "Explore") setCountry(null); setPage(p); };
  return <div className="app">
    <div className="rail-wrap">
      <nav className="rail" aria-label="Main"><h1>GeoGuessr Trainer</h1>
        {Object.keys(PAGES).map((p) => <button key={p} className={p === page ? "on" : ""} aria-current={p === page ? "page" : undefined} onClick={() => go(p)}>{p}</button>)}</nav>
    </div>
    <main className={page === "Explore" ? "bleed" : undefined}><Page country={country} onSelect={setCountry} /></main>
  </div>;
}
