import { createRoot } from "react-dom/client";
import App from "./app.jsx";
import { ToastProvider } from "./components/Toast.jsx";
import { SyncProvider } from "./lib/sync.jsx";
import "./index.css";
import "./ui.css";

createRoot(document.getElementById("root")).render(
  <ToastProvider><SyncProvider><App /></SyncProvider></ToastProvider>,
);
