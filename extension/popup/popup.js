const DEFAULTS = { enabled: false, hud: true, zoom: 12, offsetMin: 25, offsetMax: 75, maps: [] };
const $ = (id) => document.getElementById(id);
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, Number.isFinite(v) ? v : lo));

function read() {
  const a = clamp(parseInt($("offsetMin").value, 10), 0, 95);
  const b = clamp(parseInt($("offsetMax").value, 10), 0, 95);
  return {
    enabled: $("enabled").checked,
    hud: $("hud").checked,
    zoom: clamp(parseInt($("zoom").value, 10), 3, 18),
    offsetMin: Math.min(a, b),
    offsetMax: Math.max(a, b),
    maps: $("maps").value.split("\n").map((s) => s.trim()).filter(Boolean),
  };
}

function render(s) {
  $("enabled").checked = s.enabled;
  $("hud").checked = s.hud;
  $("zoom").value = s.zoom;
  $("zoomVal").textContent = s.zoom;
  $("offsetMin").value = s.offsetMin;
  $("offsetMax").value = s.offsetMax;
  $("maps").value = s.maps.join("\n");
  $("status").textContent = s.enabled && !s.maps.length ? "Agregá al menos un mapa, si no no se aplica en ninguno." : "";
}

// While typing, don't rewrite the fields (that would trim spaces and swallow Enter in the maps box).
const save = () => {
  const s = read();
  $("zoomVal").textContent = s.zoom;
  $("status").textContent = s.enabled && !s.maps.length ? "Agregá al menos un mapa, si no no se aplica en ninguno." : "";
  chrome.storage.sync.set(s);
};

chrome.storage.sync.get(DEFAULTS).then(render);
for (const id of ["enabled", "hud", "zoom", "offsetMin", "offsetMax", "maps"]) $(id).addEventListener("input", save);
for (const b of document.querySelectorAll(".presets button")) {
  b.addEventListener("click", () => { $("zoom").value = b.dataset.z; save(); });
}
