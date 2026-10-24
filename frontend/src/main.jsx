import { createRoot } from "react-dom/client";
import App from "./app.jsx";
import { ToastProvider } from "./components/Toast.jsx";
import { initMapProvider } from "./lib/mapProviders.js";
import { SyncProvider } from "./lib/sync.jsx";
import "./index.css";
import "./ui.css";

const render = () => createRoot(document.getElementById("root")).render(
  <ToastProvider><SyncProvider><App /></SyncProvider></ToastProvider>,
);

// The map source (Settings) must be known before the first map is drawn. It never throws: with no backend it is OSM.
initMapProvider().then(render);
