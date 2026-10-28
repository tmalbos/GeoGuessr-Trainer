// ==UserScript==
// @name         GeoGuessr Pinpoint Trainer
// @namespace    ggpt
// @version      0.4.0
// @description  Adds a small down arrow attached to the Play button on each map page (/maps/<id>) to turn on training for that map: at the start of each round, the minimap is centered at the chosen zoom with a random offset from the real location.
// @match        https://www.geoguessr.com/*
// @run-at       document-start
// @grant        unsafeWindow
// @grant        GM_getValue
// @grant        GM_setValue
// @noframes
// ==/UserScript==

// Per-map settings live in GM storage: { [mapId]: { enabled, zoom } }.
// Only runs on normal /game/ pages (never duels, never live modes) and only for maps switched on.
(() => {
  const W = unsafeWindow;
  if (W.__ggpt) return;
  W.__ggpt = true;

  const OFFSET_MIN = 20; // % of half the minimap window
  const OFFSET_MAX = 80;
  const ZOOM_MIN = 11;
  const ZOOM_MAX = 17;
  const DEFAULT_ZOOM = 14;
  // Half of the smaller side of the minimap window, in px (775 * 0.6 / 2).
  const HALF_PX = 232;
  const APPLY_EVERY_MS = 250;
  const WINDOW_MS = 5000; // GeoGuessr resets the minimap as the round starts: keep applying for 5 s
  const POLL_MS = 3000;

  // ── Per-map settings ──────────────────────────────────────────────────────
  const loadStore = () => {
    try { const s = GM_getValue("maps", {}); return s && typeof s === "object" ? s : {}; } catch { return {}; }
  };
  let store = loadStore();
  const getMap = (id) => {
    const m = { enabled: false, zoom: DEFAULT_ZOOM, ...(store[id] || {}) };
    m.zoom = Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Number.isFinite(m.zoom) ? m.zoom : DEFAULT_ZOOM));
    return m;
  };
  const setMap = (id, patch) => {
    store = { ...store, [id]: { ...getMap(id), ...patch } };
    try { GM_setValue("maps", store); } catch { /* ignore */ }
  };
  const anyEnabled = () => Object.values(store).some((m) => m?.enabled);

  // ── Capture the minimap ───────────────────────────────────────────────────
  const maps = new Set();
  const register = (m) => { if (m && typeof m === "object") maps.add(m); };

  function patchGoogleMaps() {
    const gm = W.google?.maps;
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
    return true;
  }
  const patchTimer = setInterval(() => { if (patchGoogleMaps()) clearInterval(patchTimer); }, 10);
  setTimeout(() => clearInterval(patchTimer), 60000);

  // The minimap is the captured map inside a "guess-map*" element. Only if no map has EVER matched
  // that class (GeoGuessr renamed it) fall back to the most recent connected map.
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

  // ── Round handling ────────────────────────────────────────────────────────
  const gameToken = () => (location.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]+)?\/)?game\/([A-Za-z0-9]+)/) || [])[1];

  let current = null; // key of the round already armed
  let game = null; // { token, mapId, skip, guessed }
  let pending = null; // { token, zoom, center, armedAt, status }
  let wasVisible = false;
  let sessionStart = 0;
  let lastTouch = 0;
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

  function offsetTarget(lat, lng, zoom) {
    const frac = (OFFSET_MIN + Math.random() * (OFFSET_MAX - OFFSET_MIN)) / 100;
    const mPerPx = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
    const dist = frac * HALF_PX * mPerPx;
    const brg = Math.random() * 2 * Math.PI;
    const R = 6371008.8, d = dist / R;
    const p1 = (lat * Math.PI) / 180, l1 = (lng * Math.PI) / 180;
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(brg));
    const l2 = l1 + Math.atan2(Math.sin(brg) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return {
      lat: Math.max(-85, Math.min(85, (p2 * 180) / Math.PI)),
      lng: ((((l2 * 180) / Math.PI + 540) % 360) + 360) % 360 - 180,
    };
  }

  // New round data: remember the view. It is applied only once the minimap is really on screen.
  function arm(token, mapId, lat, lng) {
    const zoom = getMap(mapId).zoom;
    const t = offsetTarget(lat, lng, zoom);
    pending = { token, zoom, center: { lat: t.lat, lng: t.lng }, armedAt: Date.now(), status: "waiting" };
  }

  // THE gate: a normal /game/ page, a standard game whose round is still open, and a map switched on.
  function active() {
    const token = gameToken();
    return !!token && game?.token === token && !game.skip && !game.guessed && getMap(game.mapId).enabled;
  }

  setInterval(() => {
    const token = gameToken();
    if (pending && pending.token !== token) pending = null;
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
    game = { token, mapId: data.map, skip: "", guessed: false };
    if (data.type && data.type !== "standard") { game.skip = `type ${data.type}`; return; }
    if (!r || typeof r.lat !== "number" || typeof r.lng !== "number") return;
    game.guessed = (data.player?.guesses?.length ?? 0) >= round || data.state === "finished";
    if (game.guessed) return;
    if (!data.map || !getMap(data.map).enabled) return;
    const key = `${token}:${round}`;
    if (key === current) return;
    current = key;
    arm(token, data.map, r.lat, r.lng);
  }

  // GeoGuessr's own game requests (immediate) + a slow poll as a fallback.
  const realFetch = W.fetch;
  W.fetch = function (...args) {
    const p = realFetch.apply(this, args);
    try {
      const url = String(args[0]?.url ?? args[0]);
      if (/\/api\/v3\/games\/[A-Za-z0-9]+(\?|$)/.test(url)) p.then((r) => r.clone().json()).then(ingest).catch(() => {});
    } catch { /* ignore */ }
    return p;
  };
  setInterval(async () => {
    const token = gameToken();
    if (!token || !anyEnabled() || document.hidden) return;
    try {
      const r = await realFetch.call(W, `/api/v3/games/${token}`, { credentials: "include" });
      if (r.ok) ingest(await r.json());
    } catch { /* ignore */ }
  }, POLL_MS);

  // ── Map page widget (/maps/<id or slug>) ──────────────────────────────────
  const mapSegment = () => (location.pathname.match(/^\/(?:[a-z]{2}(?:-[a-z]+)?\/)?maps\/([A-Za-z0-9_-]+)\/?$/) || [])[1];
  const idCache = {}; // segment -> id | null (failed) | undefined (pending)
  function resolveMapId(seg) {
    if (/^[a-f0-9]{24}$/i.test(seg)) return seg;
    if (!(seg in idCache)) {
      idCache[seg] = undefined;
      realFetch.call(W, `/api/maps/${seg}`, { credentials: "include" })
        .then((r) => (r.ok ? r.json() : null))
        .then((j) => { idCache[seg] = typeof j?.id === "string" ? j.id : null; })
        .catch(() => { idCache[seg] = null; });
    }
    return idCache[seg];
  }

  const PLAY_RE = /^(play|jugar|jouer|spielen|jogar|giocare|играть)$/i;
  function findPlayButton() {
    for (const el of document.querySelectorAll("button, a")) {
      if (PLAY_RE.test((el.textContent || "").trim()) && el.getBoundingClientRect().width > 0) return el;
    }
    return null;
  }

  const ARROW_CSS = `
    :host{display:inline-block;vertical-align:top;position:relative}
    *{box-sizing:border-box}
    button{display:flex;align-items:center;justify-content:center;width:36px;margin:0;padding:0;border:0;cursor:pointer;
      font:inherit;position:relative;pointer-events:auto}
    button:hover{filter:brightness(1.15)}
    svg{width:16px;height:16px;pointer-events:none}
    .dot{position:absolute;top:5px;right:5px;width:7px;height:7px;border-radius:50%;background:#3fb97a;display:none;pointer-events:none}
    .on .dot{display:block}
  `;
  const PANEL_CSS = `
    :host{position:fixed;z-index:2147483647;display:none}
    .pn{width:260px;padding:14px;font:13px/1.4 system-ui,sans-serif;background:#0d1117;color:#e6edf3;
      border:1px solid #2a3340;border-radius:10px;box-shadow:0 8px 24px #000a}
    label.r{display:flex;align-items:center;gap:8px;margin:0 0 10px;cursor:pointer}
    input[type=range]{width:100%}
  `;

  let widget = null; // { id, seg, since, arrowHost, arrowBtn, panelHost, refresh }
  let panelOpen = false;

  const swallow = (el) => {
    for (const t of ["pointerdown", "pointerup", "mousedown", "mouseup", "click"]) el.addEventListener(t, (e) => e.stopPropagation());
  };

  function buildWidget(id, seg) {
    // Arrow attached to the Play button
    const arrowHost = document.createElement("div");
    const ar = arrowHost.attachShadow({ mode: "open" });
    ar.innerHTML = `<style>${ARROW_CSS}</style>
      <button type="button" title="Training options" aria-label="Training options">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
        <span class="dot"></span>
      </button>`;
    const arrowBtn = ar.querySelector("button");

    // Panel: its own fixed-position element so no parent can clip it or block clicks
    const panelHost = document.createElement("div");
    const pr = panelHost.attachShadow({ mode: "open" });
    pr.innerHTML = `<style>${PANEL_CSS}</style>
      <div class="pn">
        <label class="r"><input type="checkbox" id="en"> <b>Enable on this map</b></label>
        <div>Initial zoom: <b id="zv"></b></div>
        <input type="range" id="zm" min="11" max="17" step="1">
      </div>`;
    const $ = (q) => pr.querySelector(q);
    const refresh = () => {
      const st = getMap(id);
      $("#en").checked = st.enabled;
      $("#zm").value = st.zoom;
      $("#zv").textContent = st.zoom;
      arrowBtn.classList.toggle("on", st.enabled);
    };
    $("#en").addEventListener("change", () => { setMap(id, { enabled: $("#en").checked }); refresh(); });
    $("#zm").addEventListener("input", () => { setMap(id, { zoom: parseInt($("#zm").value, 10) }); refresh(); });
    arrowBtn.addEventListener("click", (e) => { e.preventDefault(); console.log("[ggpt] arrow clicked"); setPanel(!panelOpen); });
    swallow(arrowHost);
    swallow(panelHost);
    refresh();
    document.documentElement.appendChild(panelHost);
    return { id, seg, since: Date.now(), arrowHost, arrowBtn, panelHost, refresh };
  }

  function setPanel(open) {
    panelOpen = open && !!widget;
    if (!widget) return;
    widget.panelHost.style.display = panelOpen ? "block" : "none";
    if (panelOpen) placePanel();
  }

  function placePanel() {
    const r = widget.arrowHost.getBoundingClientRect();
    const left = Math.max(8, Math.min(innerWidth - 268, r.right - 260));
    widget.panelHost.style.left = `${left}px`;
    widget.panelHost.style.top = `${r.bottom + 8}px`;
  }

  // Close on outside click / Escape (registered once)
  document.addEventListener("pointerdown", (e) => {
    if (!panelOpen || !widget) return;
    const path = e.composedPath();
    if (!path.includes(widget.panelHost) && !path.includes(widget.arrowHost)) setPanel(false);
  }, true);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setPanel(false); }, true);

  function removeWidget() {
    if (!widget) return;
    widget.arrowHost.remove();
    widget.panelHost.remove();
    widget = null;
    panelOpen = false;
  }

  // Make the arrow look like part of the Play button: same colors, height and corner radius.
  function matchPlayStyle(play) {
    const cs = getComputedStyle(play);
    if (!play.dataset.ggptRadius) play.dataset.ggptRadius = `${cs.borderTopRightRadius}|${cs.borderBottomRightRadius}`;
    const h = play.getBoundingClientRect().height;
    const round = (v) => { const n = parseFloat(v); return !n || n < 8 ? `${h / 2}px` : v; };
    const [tr, br] = play.dataset.ggptRadius.split("|");
    const b = widget.arrowBtn.style;
    b.background = cs.background;
    b.backgroundColor = cs.backgroundColor;
    b.color = cs.color;
    b.height = `${h}px`;
    b.borderRadius = `0 ${round(tr)} ${round(br)} 0`;
    b.boxShadow = cs.boxShadow;
    b.borderTop = cs.borderTop;
    b.borderBottom = cs.borderBottom;
    b.borderLeft = "1px solid rgba(0,0,0,.28)";
  }

  // The arrow sits on top of Play's right end (nothing in the layout moves). Play gets extra right padding
  // so its label stays centered in the part that is still visible.
  const ARROW_W = 36;
  function padPlay(play) {
    if (play.dataset.ggptPad) return;
    const base = parseFloat(getComputedStyle(play).paddingRight) || 0;
    play.dataset.ggptPad = "1";
    play.style.paddingRight = `${base + ARROW_W}px`;
  }

  function placeArrow() {
    const w = widget;
    if (!w?.play?.isConnected) return;
    const r = w.play.getBoundingClientRect();
    const st = w.arrowHost.style;
    if (r.width === 0 || r.height === 0) { st.display = "none"; return; }
    st.cssText = `position:fixed;z-index:1000;display:block;left:${r.right - ARROW_W}px;top:${r.top}px`;
    w.arrowBtn.style.height = `${r.height}px`;
  }
  const frame = () => { placeArrow(); requestAnimationFrame(frame); };
  requestAnimationFrame(frame);

  setInterval(() => {
    const seg = mapSegment();
    if (!seg) { removeWidget(); return; }
    if (widget && widget.seg !== seg) removeWidget();
    const id = resolveMapId(seg);
    if (!id) return;
    if (!widget) widget = buildWidget(id, seg);
    const play = findPlayButton();
    if (play) {
      widget.play = play;
      padPlay(play);
      if (!widget.arrowHost.isConnected) document.documentElement.appendChild(widget.arrowHost);
      matchPlayStyle(play);
    } else if (!widget.arrowHost.isConnected && Date.now() - widget.since > 3000) {
      // Fallback: couldn't find the Play button, float the arrow instead.
      widget.arrowHost.style.cssText = "position:fixed;top:90px;right:16px;z-index:1000";
      widget.arrowBtn.style.cssText = "height:36px;border-radius:8px;background:#1d242d;color:#e6edf3";
      document.documentElement.appendChild(widget.arrowHost);
    }
    if (panelOpen) placePanel();
  }, 500);
})();
