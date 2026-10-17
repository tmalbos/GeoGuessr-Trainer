// Isolated world: the only script that can read chrome.storage. Forwards settings to inject.js.
const DEFAULTS = { enabled: false, hud: true, zoom: 12, offsetMin: 25, offsetMax: 75, maps: [] };

const send = (settings) => window.postMessage({ ggpt: "settings", settings }, "*");
const load = () => chrome.storage.sync.get(DEFAULTS).then(send);

window.addEventListener("message", (e) => {
  if (e.source === window && e.data?.ggpt === "request") load();
});
chrome.storage.onChanged.addListener(load);
load();
