import { useState } from "react";
import Analysis from "./pages/Analysis/index.jsx";
import Dashboard from "./pages/Dashboard.jsx";
import History from "./pages/History.jsx";
import Settings from "./pages/Settings.jsx";
import Study from "./pages/Study/index.jsx";
import Sync from "./pages/Sync.jsx";

const PAGES = { Dashboard, Sync, Analysis, History, Study, Settings };

export default function App() {
  const [page, setPage] = useState("Dashboard");
  const Page = PAGES[page];
  return <div className="app">
    <nav className="rail"><h1>GeoGuessr Trainer</h1>
      {Object.keys(PAGES).map((p) => <button key={p} className={p === page ? "on" : ""} onClick={() => setPage(p)}>{p}</button>)}</nav>
    <main><Page /></main>
  </div>;
}
