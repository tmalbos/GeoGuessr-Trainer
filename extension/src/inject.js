// Runs inside the GeoGuessr page (MAIN world, document_start).
// 1. Captures the Google Maps instance of the guess minimap.
// 2. Reads the current round's real location from GeoGuessr's own game API.
// 3. Once the minimap is on screen, sets its zoom and centers it on a random offset from the real spot.
// Only runs on normal /game/ pages (never duels, never live modes) and only for the maps the user listed.
// A small status badge at the bottom shows what it is doing; its button re-applies the view.
(() => {
  if (window.__ggpt) return;
  window.__ggpt = true;

  const DEFAULTS = { enabled: false, hud: true, zoom: 12, offsetMin: 25, offsetMax: 75, maps: [] };
  // Half of the smaller side of the minimap window, in px. Same reference the main app uses
  // (MAP_PX = 1245x775 * 0.6 in replay_behavior.py): 775 * 0.6 / 2.
  const HALF_PX = 232;
  const APPLY_EVERY_MS = 250;
  const WINDOW_MS = 5000; // GeoGuessr resets the minimap as the round starts: keep applying for 5 s
  const POLL_MS = 3000;

  let cfg = { ...DEFAULTS };
  let settingsReady = false;
  const norm = (s) => String(s ?? "").trim().toLowerCase();
  const listed = (name) => cfg.maps.map(norm).includes(norm(name));

  // ── Settings (from bridge.js) ─────────────────────────────────────────────
  window.addEventListener("message", (e) => {
    if (e.source === window && e.data?.ggpt === "settings") {
      cfg = { ...DEFAULTS, ...e.data.settings };
      settingsReady = true;
    }
  });
  window.postMessage({ ggpt: "request" }, "*");

  // ── Capture the minimap ───────────────────────────────────────────────────
  const maps = new Set();
  const register = (m) => { if (m && typeof m === "object") maps.add(m); };
  let patched = false;

  function patchGoogleMaps() {
    const gm = window.google?.maps;
    if (!gm || typeof gm.Map !== "function") return false;
    const proto = gm.Map.prototype;
    for (const k of ["setCenter", "setZoom", "panTo", "getCenter", "getZoom", "setOptions", "fitBounds", "getDiv"]) {
      const orig = proto[k];
      if (typeof orig !== "function" || orig.__ggpt) continue;
      const wrapped = function (...args) { register(this); return orig.apply(this, args); };
      wrapped.__ggpt = true;
      try { proto[k] = wrapped; } catch { /* non-writable */ }
    }
    try {
      gm.Map = new Proxy(gm.Map, {
        construct(target, args, nt) { const m = Reflect.construct(target, args, nt); register(m); return m; },
      });
    } catch { /* non-writable: the prototype hooks still catch it */ }
    patched = true;
    return true;
  }
  const patchTimer = setInterval(() => { if (patchGoogleMaps()) clearInterval(patchTimer); }, 10);
  setTimeout(() => clearInterval(patchTimer), 60000);

  // The minimap is the captured map inside a "guess-map*" element. Only if no map has EVER matched
  // that class (GeoGuessr renamed it) fall back to the most recent connected map.
  // Maps are never dropped from the set: GeoGuessr reuses the same one across rounds and
  // detaches it from the page in between.
  let everMatched = false;
  function guessMap() {
    let byClass = null, last = null;
    for (const m of maps) {
      try {
        const d = m.getDiv();
        if (!d?.isConnected) continue;
        last = m;
        if (d.closest('[class*="guess-map"]')) { byClass = m; everMatched = true; }
      } catch { /* ignore */ }
    }
    return byClass || (everMatched ? null : last);
  }
  const isVisible = (m) => {
    try { const r = m.getDiv().getBoundingClientRect(); return r.width > 0 && r.height > 0; } catch { return false; }
  };
  const connectedMaps = () => [...maps].filter((m) => { try { return m.getDiv().isConnected; } catch { return false; } }).length;

  // ── Round handling ────────────────────────────────────────────────────────
  // Only standard single-player games: /game/{token} (optionally with a locale prefix).
  const gameToken = () => (location.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]+)?\/)?game\/([A-Za-z0-9]+)/) || [])[1];

  let current = null; // key of the round already armed
  let lastRound = null; // { token, lat, lng } of the round in progress (for the HUD button)
  let game = null; // { token, mapName, skip, guessed } from the last game response
  let pending = null; // the view to apply: { zoom, center, km, armedAt, status }
  let wasVisible = false;
  let sessionStart = 0; // last moment the minimap became visible (= a round actually started)
  let lastTouch = 0; // last time the user used the minimap
  const watched = new WeakSet();

  function watch(map) {
    const div = map.getDiv();
    if (watched.has(div)) return;
    watched.add(div);
    const stop = (e) => { if (e.isTrusted) lastTouch = Date.now(); };
    div.addEventListener("pointerdown", stop, true);
    div.addEventListener("wheel", stop, true);
  }

  function flash(map) {
    try {
      const d = map.getDiv();
      const host = d.closest('[class*="guess-map"]') || d;
      const prev = host.style.boxShadow;
      host.style.boxShadow = "0 0 0 4px #3fb97a, 0 0 26px 6px #3fb97a";
      setTimeout(() => { host.style.boxShadow = prev; }, 1400);
    } catch { /* ignore */ }
  }

  function offsetTarget(lat, lng) {
    const lo = Math.min(cfg.offsetMin, cfg.offsetMax) / 100;
    const hi = Math.max(cfg.offsetMin, cfg.offsetMax) / 100;
    const frac = lo + Math.random() * (hi - lo);
    const mPerPx = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** cfg.zoom; // Web Mercator, 256 px tiles
    const dist = frac * HALF_PX * mPerPx;
    const brg = Math.random() * 2 * Math.PI;
    const R = 6371008.8, d = dist / R;
    const p1 = (lat * Math.PI) / 180, l1 = (lng * Math.PI) / 180;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(brg));
    const l2 = l1 + Math.atan2(Math.sin(brg) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return {
      lat: Math.max(-85, Math.min(85, (p2 * 180) / Math.PI)),
      lng: ((((l2 * 180) / Math.PI + 540) % 360) + 360) % 360 - 180,
      distKm: dist / 1000,
    };
  }

  // New round data (or the HUD button): remember the view. It is applied only once the minimap is
  // really on screen, so sitting on the results screen never uses up the apply window.
  function arm(token, lat, lng) {
    const t = offsetTarget(lat, lng);
    pending = { token, zoom: cfg.zoom, center: { lat: t.lat, lng: t.lng }, km: t.distKm, armedAt: Date.now(), status: "waiting" };
  }

  // THE gate. Nothing is ever applied (by the loop or by the badge button) unless all of this holds
  // right now: extension on, a normal /game/ page, a standard game whose round is still open,
  // and a map from the user's list. Duels, daily/live modes and everything else fail the URL test.
  function active() {
    const token = gameToken();
    return cfg.enabled && !!token && game?.token === token && !game.skip && !game.guessed && listed(game.mapName);
  }

  setInterval(() => {
    // The page is a SPA: forget everything that belongs to another page.
    const token = gameToken();
    if (pending && pending.token !== token) pending = null;
    if (lastRound && lastRound.token !== token) lastRound = null;
    if (game && game.token !== token) game = null;

    const m = guessMap();
    const vis = !!m && isVisible(m);
    if (vis && !wasVisible) sessionStart = Date.now();
    wasVisible = vis;
    if (m) watch(m);
    const p = pending;
    if (!p || !vis || !active()) return;
    const from = Math.max(sessionStart, p.armedAt);
    if (Date.now() - from > WINDOW_MS || lastTouch >= from) return;
    try { m.setZoom(p.zoom); m.setCenter(p.center); } catch { return; }
    if (p.status !== "ok") { p.status = "ok"; flash(m); }
  }, APPLY_EVERY_MS);

  function ingest(data) {
    if (!data || !Array.isArray(data.rounds)) return;
    const token = gameToken();
    if (!token || (data.token && data.token !== token)) return;
    const round = data.round;
    const r = data.rounds[round - 1];
    game = { token, mapName: data.mapName, skip: "", guessed: false };
    if (data.type && data.type !== "standard") { game.skip = `tipo ${data.type}`; return; }
    if (!r || typeof r.lat !== "number" || typeof r.lng !== "number") return;
    game.guessed = (data.player?.guesses?.length ?? 0) >= round || data.state === "finished";
    if (game.guessed) return;
    lastRound = { token, lat: r.lat, lng: r.lng };
    if (!cfg.enabled || !listed(data.mapName)) return; // only the maps the user listed
    const key = `${token}:${round}`;
    if (key === current) return;
    current = key;
    arm(token, r.lat, r.lng);
  }

  // GeoGuessr's own game requests (immediate) + a slow poll as a fallback.
  const realFetch = window.fetch;
  window.fetch = function (...args) {
    const p = realFetch.apply(this, args);
    try {
      const url = String(args[0]?.url ?? args[0]);
      if (/\/api\/v3\/games\/[A-Za-z0-9]+(\?|$)/.test(url)) p.then((r) => r.clone().json()).then(ingest).catch(() => {});
    } catch { /* ignore */ }
    return p;
  };
  setInterval(async () => {
    const token = gameToken();
    if (!token || !cfg.enabled || document.hidden) return;
    try {
      const r = await realFetch(`/api/v3/games/${token}`, { credentials: "include" });
      if (r.ok) ingest(await r.json());
    } catch { /* ignore */ }
  }, POLL_MS);

  // ── Status badge ──────────────────────────────────────────────────────────
  const COLORS = { ok: "#3fb97a", warn: "#e6b84a", bad: "#ff7357", off: "#6b7686" };

  function headline() {
    if (!settingsReady) return ["bad", "sin configuración (recargá la pestaña)"];
    if (!cfg.enabled) return ["off", "desactivada"];
    if (!gameToken()) return ["off", "inactiva en esta pantalla (solo funciona en partidas /game/)"];
    if (!patched) return ["warn", "esperando Google Maps…"];
    if (!game) return ["warn", "esperando datos de la partida…"];
    if (game.skip) return ["off", `no aplica (${game.skip})`];
    if (!listed(game.mapName)) return ["bad", `"${game.mapName}" no está en tu lista de mapas`];
    if (game.guessed) return ["warn", "ronda terminada · se aplica en la próxima"];
    if (!pending) return ["warn", "esperando inicio de ronda…"];
    if (pending.status === "ok") return ["ok", `zoom ${pending.zoom} · offset ${pending.km.toFixed(1)} km aplicado`];
    if (!connectedMaps() && Date.now() - pending.armedAt > 8000) return ["bad", "no encuentro el minimapa"];
    return ["warn", "listo · se aplica cuando arranque la ronda"];
  }

  let hud = null;
  function mountHud() {
    const host = document.createElement("div");
    host.style.cssText = "position:fixed;left:50%;bottom:10px;transform:translateX(-50%);z-index:2147483647;";
    const root = host.attachShadow({ mode: "open" });
    root.innerHTML = `
      <style>
        .box{display:flex;align-items:center;gap:8px;font:12px/1.4 system-ui,sans-serif;color:#e6edf3;background:#161b22ee;
          border:1px solid #2a3340;border-radius:10px;padding:6px 10px;max-width:380px;box-shadow:0 6px 18px #0008}
        i{width:10px;height:10px;border-radius:50%;flex:none}
        span{flex:1}
        button{background:none;border:1px solid #2a3340;color:inherit;border-radius:6px;padding:0 6px;cursor:pointer}
        button:hover{border-color:#6cc0e5}
      </style>
      <div class="box"><i></i><span></span>
        <button title="Reaplicar en esta ronda">↻</button></div>`;
    root.querySelector("button").addEventListener("click", () => { if (lastRound && active()) arm(lastRound.token, lastRound.lat, lastRound.lng); });
    hud = { host, dot: root.querySelector("i"), text: root.querySelector("span") };
    document.documentElement.appendChild(host);
  }

  function renderHud() {
    if (!cfg.hud) { if (hud) hud.host.style.display = "none"; return; }
    if (!hud) mountHud();
    if (!hud.host.isConnected) document.documentElement.appendChild(hud.host);
    hud.host.style.display = "";
    const [kind, text] = headline();
    hud.dot.style.background = COLORS[kind];
    hud.text.textContent = `Pinpoint Trainer: ${text}`;
  }
  setInterval(renderHud, 500);
  renderHud();
})();
