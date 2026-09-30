import { useState } from "react";
import Analysis from "./pages/Analysis/index.jsx";
import History from "./pages/History.jsx";
import Settings from "./pages/Settings.jsx";
import Study from "./pages/Study/index.jsx";
import Sync from "./pages/Sync.jsx";

const PAGES = { Sync, History, Analysis, Study, Settings };

export default function App() {
  const [page, setPage] = useState("Sync");
  const Page = PAGES[page];
  return <div className="app">
    <div className="rail-wrap">
      <nav className="rail" aria-label="Main"><h1>GeoGuessr Trainer</h1>
        {Object.keys(PAGES).map((p) => <button key={p} className={p === page ? "on" : ""} aria-current={p === page ? "page" : undefined} onClick={() => setPage(p)}>{p}</button>)}</nav>
    </div>
    <main><Page /></main>
  </div>;
}
