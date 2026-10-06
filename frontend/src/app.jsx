import SyncChip from "./components/SyncChip.jsx";
import { navigate, useHashRoute } from "./lib/router.js";
import { useSync } from "./lib/sync.jsx";
import Analysis from "./pages/Analysis/index.jsx";
import Explore from "./pages/Explore/index.jsx";
import History from "./pages/History.jsx";
import Minigames from "./pages/Minigames/index.jsx";
import Settings from "./pages/Settings.jsx";
import Study from "./pages/Study/index.jsx";

// [url key, rail label, component]. The URL is the source of truth: #/explore/AR, #/analysis/country, ...
const PAGES = [
  ["explore", "Explore", Explore],
  ["history", "History", History],
  ["analysis", "Analysis", Analysis],
  ["study", "Study", Study],
  ["minigames", "Minigames", Minigames],
  ["settings", "Settings", Settings],
];

export default function App() {
  const { segments, query } = useHashRoute();
  const [key, ...seg] = segments;
  const [page, , Page] = PAGES.find((p) => p[0] === key) ?? PAGES[0];
  const { running, types, start } = useSync();
  return <div className="app">
    <div className="rail-wrap">
      <nav className="rail" aria-label="Main"><h1>GeoGuessr Trainer</h1>
        {PAGES.map(([k, label]) => <button key={k} className={k === page ? "on" : ""} aria-current={k === page ? "page" : undefined} onClick={() => navigate(`/${k}`)}>{label}</button>)}
        <button type="button" className="rail-sync" onClick={start} disabled={running || !types.length}>
          <span className={`split-icon ${running ? "spin" : ""}`} aria-hidden="true">↻</span>{running ? "Syncing…" : "Sync games"}
        </button>
      </nav>
    </div>
    <main className={page === "explore" ? "bleed" : undefined}><Page seg={seg} query={query} /></main>
    <SyncChip />
  </div>;
}
