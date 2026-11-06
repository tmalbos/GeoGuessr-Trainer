import { createRoot } from "react-dom/client";
import App from "./app.jsx";
import { ToastProvider } from "./components/Toast.jsx";
import { initMapProvider } from "./lib/mapProviders.js";
import { SyncProvider } from "./lib/sync.jsx";
import "./index.css";
import "./ui.css";

const root = createRoot(document.getElementById("root"));
const POLL_MS = 1000;

function Boot({ message, onRetry }) {
  return <div style={{ maxWidth: 560, margin: "15vh auto", padding: "0 20px" }}>
    <div className="card">
      <b>GeoGuessr Trainer</b>
      <p className="muted">{message}</p>
      {onRetry && <button type="button" className="btn ghost" onClick={onRetry}>Retry</button>}
    </div>
  </div>;
}

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** One readiness probe. Anything unexpected counts as "not ready yet"; the boot screen stays up. */
async function readReady() {
  let r;
  try {
    r = await fetch("/api/ready");
  } catch {
    return false;
  }
  if (r.status === 503) {
    const body = await r.json().catch(() => null);
    if (body?.checks?.geodata === "error") throw new Error("Geo layers failed to load.");
    return false;
  }
  if (r.status >= 500) return false;
  if (!r.ok) throw new Error(`Readiness check failed (${r.status}).`);
  const body = await r.json().catch(() => null);
  if (body?.checks?.geodata === "error") throw new Error("Geo layers failed to load.");
  return body?.ready === true;
}

async function boot() {
  try {
    while (!(await readReady())) {
      root.render(<Boot message="Starting…" />);
      await wait(POLL_MS);
    }
    root.render(<Boot message="Loading your settings…" />);
    await initMapProvider();
    root.render(
      <ToastProvider><SyncProvider><App /></SyncProvider></ToastProvider>,
    );
  } catch (e) {
    console.error(e);
    root.render(<Boot message="The app couldn't start." onRetry={boot} />);
  }
}

boot();
