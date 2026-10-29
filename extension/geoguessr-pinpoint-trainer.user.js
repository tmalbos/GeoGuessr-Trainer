// ==UserScript==
// @name         GeoGuessr Pinpoint Trainer
// @namespace    ggpt
// @version      0.7.2
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

  const OFFSET_MIN = 10; // % of half the minimap window
  const OFFSET_MAX = 90;
  const ZOOM_MIN = 11;
  const ZOOM_MAX = 17;
  const DEFAULT_ZOOM = 14;
  // Half of the smaller side of the minimap window, in px (775 * 0.6 / 2).
  const HALF_PX = 232;
  const APPLY_EVERY_MS = 250;
  const WINDOW_MS = 5000; // GeoGuessr resets the minimap as the round starts: keep applying for 5 s
  const POLL_MS = 3000;
  // Score model (S = 5000 * exp(-10 * d / D), D = the map's scoring distance; always 5000 under 25 m).
  const DEFAULT_D = 14916862; // world-size map, meters. Replaced by the real value of each map when known.
  // Every guess is replaced before it is sent, so the server only ever records 1 point (the guess was a 5k) or
  // 0 points (it wasn't). The page you look at still shows your real click, distance and score.
  // The server rounds scores, so anything in [0.5, 1.5) is recorded as 1. Aim at the middle of that window
  // (in log scale) so a small error in D still lands on 1.
  const HIT_TARGET = Math.sqrt(0.5 * 1.5);
  const MISS_DIST = 19500000; // m: far enough from the real spot (the antipode is ~20,015 km) to always score 0

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

  // ── Geometry & scoring ────────────────────────────────────────────────────
  const R_EARTH = 6371008.8;
  const toRad = (x) => (x * Math.PI) / 180;
  function haversine(a, b) {
    const p1 = toRad(a.lat), p2 = toRad(b.lat);
    const dp = p2 - p1, dl = toRad(b.lng - a.lng);
    const h = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    return 2 * R_EARTH * Math.asin(Math.min(1, Math.sqrt(h)));
  }
  function destPoint(lat, lng, dist, brg) {
    const d = dist / R_EARTH;
    const p1 = toRad(lat), l1 = toRad(lng);
    const p2 = Math.asin(Math.sin(p1) * Math.cos(d) + Math.cos(p1) * Math.sin(d) * Math.cos(brg));
    const l2 = l1 + Math.atan2(Math.sin(brg) * Math.sin(d) * Math.cos(p1), Math.cos(d) - Math.sin(p1) * Math.sin(p2));
    return {
      lat: (p2 * 180) / Math.PI,
      lng: ((((l2 * 180) / Math.PI + 540) % 360) + 360) % 360 - 180,
    };
  }
  const scoreFor = (d, D) => (d < 25 ? 5000 : Math.max(0, Math.round(5000 * Math.exp((-10 * d) / D))));

  function offsetTarget(lat, lng, zoom) {
    // Uniform over the AREA of the ring between OFFSET_MIN% and OFFSET_MAX% of the radius: wider rings are
    // proportionally more likely, so the radius is the square root of a value uniform in r^2.
    const lo = (OFFSET_MIN / 100) ** 2, hi = (OFFSET_MAX / 100) ** 2;
    const frac = Math.sqrt(lo + Math.random() * (hi - lo));
    const mPerPx = (156543.03392 * Math.cos((lat * Math.PI) / 180)) / 2 ** zoom;
    const p = destPoint(lat, lng, frac * HALF_PX * mPerPx, Math.random() * 2 * Math.PI);
    return { lat: Math.max(-85, Math.min(85, p.lat)), lng: p.lng };
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

  // ── Scoring distance D of the current map ─────────────────────────────────
  // GeoGuessr does not send D. It is measured from the server's own score + distance of any earlier guess
  // (D = -10 * d / ln(score / 5000)), keeping the most precise measurement ever seen for each map.
  // Before any guess exists the diagonal of the map's bounds is used as an estimate.
  let Dmap = {};
  try { Dmap = GM_getValue("D", {}) || {}; } catch { /* ignore */ }
  const savedD = (id) => { const v = Dmap[id]; return typeof v === "number" ? { D: v, err: 1 } : v; };
  function calibrateD(data) {
    let best = null;
    for (const g of data.player?.guesses || []) {
      const sc = g?.roundScoreInPoints, d = g?.distanceInMeters;
      if (typeof sc !== "number" || typeof d !== "number" || sc < 1 || sc > 4995 || !(d > 0)) continue;
      const ln = Math.abs(Math.log(sc / 5000));
      const err = 0.5 / sc / ln; // the score is rounded to an integer: this is the relative error it causes
      if (!best || err < best.err) best = { D: (10 * d) / ln, err };
    }
    return best;
  }
  function boundsD(data) {
    const b = data.bounds;
    if (!b?.min || !b?.max) return null;
    return haversine(b.min, b.max);
  }
  function mapD(data) {
    const cal = calibrateD(data), saved = savedD(data.map);
    if (cal && (!saved || cal.err < saved.err)) {
      if (data.map) {
        Dmap = { ...Dmap, [data.map]: cal };
        try { GM_setValue("D", Dmap); } catch { /* ignore */ }
      }
      return cal.D;
    }
    if (saved) return saved.D;
    return boundsD(data) || DEFAULT_D;
  }

  function ingest(data) {
    if (!data || !Array.isArray(data.rounds)) return;
    const token = gameToken();
    if (!token || (data.token && data.token !== token)) return;
    const round = data.round;
    const r = data.rounds[round - 1];
    game = { token, mapId: data.map, skip: "", guessed: false, round };
    if (data.type && data.type !== "standard") { game.skip = `type ${data.type}`; return; }
    if (!r || typeof r.lat !== "number" || typeof r.lng !== "number") return;
    game.loc = { lat: r.lat, lng: r.lng };
    game.D = mapD(data);
    game.guessed = (data.player?.guesses?.length ?? 0) >= round || data.state === "finished";
    if (game.guessed) return;
    if (!data.map || !getMap(data.map).enabled) return;
    const key = `${token}:${round}`;
    if (key === current) return;
    current = key;
    arm(token, data.map, r.lat, r.lng);
  }

  // ── Guess swap: a guess that would score 5000 is replaced before it is sent ─
  const loadSwaps = () => { try { const x = GM_getValue("swaps", {}); return x && typeof x === "object" ? x : {}; } catch { return {}; } };
  let swaps = loadSwaps(); // { [token]: { [round]: { real: {lat,lng,d,score}, sent: {lat,lng} } } }
  function saveSwap(token, round, rec) {
    swaps = { ...swaps, [token]: { ...(swaps[token] || {}), [round]: rec } };
    const keys = Object.keys(swaps);
    for (const k of keys.slice(0, Math.max(0, keys.length - 20))) delete swaps[k];
    try { GM_setValue("swaps", swaps); } catch { /* ignore */ }
  }

  // Decides what to send for a guess body (JSON text). Returns the new body text, or null to leave it alone.
  function swapBodyText(text) {
    const body = JSON.parse(text);
    if (typeof body?.lat !== "number" || typeof body?.lng !== "number") return null; // no pin on the map: nothing to swap
    // (a guess auto-sent when the timer runs out has timedOut:true but still carries the pin, so it is swapped too)
    if (!active() || !game.loc || !game.D) {
      console.log("[ggpt] guess seen, not touched: map not enabled or round data missing", { game });
      return null;
    }
    const real = { lat: body.lat, lng: body.lng };
    const d = haversine(real, game.loc);
    const score = scoreFor(d, game.D);
    const fived = score >= 5000;
    // 1 point = a distance whose score rounds to 1. 0 points = as far as possible.
    const dSent = fived ? Math.min(MISS_DIST, (game.D / 10) * Math.log(5000 / HIT_TARGET)) : MISS_DIST;
    const sent = destPoint(game.loc.lat, game.loc.lng, dSent, Math.random() * 2 * Math.PI);
    saveSwap(game.token, game.round, { real: { ...real, d, score }, sent });
    console.log(`[ggpt] guess ${fmtDist(d)} away = ${score} pts (D=${Math.round(game.D / 1000)} km) -> sent as ${fived ? "1 point (5k)" : "0 points"} | your click ${real.lat.toFixed(5)},${real.lng.toFixed(5)} -> sent ${sent.lat.toFixed(5)},${sent.lng.toFixed(5)}`);
    return JSON.stringify({ ...body, lat: sent.lat, lng: sent.lng });
  }

  // fetch() version
  async function rewriteGuess(args) {
    try {
      const [input, init] = args;
      let text = null;
      if (typeof init?.body === "string") text = init.body;
      else if (input instanceof W.Request) text = await input.clone().text();
      if (!text) { console.log("[ggpt] guess seen but body unreadable, not touched"); return args; }
      const newText = swapBodyText(text);
      if (!newText) return args;
      if (typeof init?.body === "string") return [input, { ...init, body: newText }];
      return [new W.Request(input, { body: newText }), ...args.slice(1)];
    } catch (e) { console.log("[ggpt] error while handling guess", e); return args; }
  }

  // What the page should show instead of what the server answered.
  const fmtDist = (m) => (m >= 1000 ? `${(m / 1000).toFixed(1)} km` : `${Math.round(m)} m`);
  const distObj = (m) => ({
    meters: m >= 1000 ? { amount: (m / 1000).toFixed(1), unit: "km" } : { amount: String(Math.round(m)), unit: "m" },
    miles: m >= 1609.344 ? { amount: (m / 1609.344).toFixed(1), unit: "miles" } : { amount: String(Math.round(m / 0.9144)), unit: "yd" },
  });
  const loggedServer = new Set();
  const shownInfo = {}; // token -> { [round]: { sent: {d, score}, real: {d, score} }, total: { sent, shown } }

  function patchGameData(data) {
    const token = gameToken();
    const sw = token && swaps[token];
    const p = data?.player;
    if (!sw || !Array.isArray(p?.guesses)) return null;
    const info = (shownInfo[token] = shownInfo[token] || {});
    const rawTotal = p.totalScoreInPoints;
    let dScore = 0, dDist = 0, changed = false;
    p.guesses.forEach((g, i) => {
      const x = sw[i + 1];
      if (!x || typeof g?.distanceInMeters !== "number") return;
      const logKey = `${token}:${i + 1}:${g.roundScoreInPoints}`;
      if (!loggedServer.has(logKey)) {
        loggedServer.add(logKey);
        console.log(`[ggpt] SERVER HAS for round ${i + 1}: ${g.roundScoreInPoints} pts, ${fmtDist(g.distanceInMeters)} from the real spot (${g.lat.toFixed(5)},${g.lng.toFixed(5)}) | the page is shown your real click instead`);
      }
      info[i + 1] = { sent: { d: g.distanceInMeters, score: g.roundScoreInPoints }, real: { d: x.real.d, score: x.real.score } };
      dScore += x.real.score - g.roundScoreInPoints;
      dDist += x.real.d - g.distanceInMeters;
      g.lat = x.real.lat;
      g.lng = x.real.lng;
      g.distanceInMeters = x.real.d;
      g.roundScoreInPoints = x.real.score;
      if (typeof g.roundScoreInPercentage === "number") g.roundScoreInPercentage = Math.round((x.real.score / 50) * 100) / 100;
      if (g.distance) g.distance = { ...g.distance, ...distObj(x.real.d) };
      changed = true;
    });
    if (!changed) return null;
    if (typeof p.totalScoreInPoints === "number") p.totalScoreInPoints += dScore;
    if (typeof p.totalDistanceInMeters === "number") {
      p.totalDistanceInMeters += dDist;
      if (p.totalDistance) p.totalDistance = { ...p.totalDistance, ...distObj(p.totalDistanceInMeters) };
    }
    info.total = { sent: rawTotal, shown: p.totalScoreInPoints };
    return data;
  }

  // Reads a game response (JSON text): remembers the round, returns the corrected JSON text (or null if unchanged).
  function processResponseText(text) {
    try {
      const data = JSON.parse(text);
      ingest(data); // always from the raw server data
      const patched = patchGameData(JSON.parse(JSON.stringify(data)));
      return patched ? JSON.stringify(patched) : null;
    } catch { return null; }
  }

  // GeoGuessr talks to its API with fetch() or XMLHttpRequest. Both are hooked.
  const GAME_URL = /\/api\/v3\/games\/[A-Za-z0-9]+(\?|$)/;
  const realFetch = W.fetch;
  W.fetch = function (...args) {
    let url = "", method = "GET";
    try {
      url = String(args[0]?.url ?? args[0]);
      method = String(args[1]?.method ?? args[0]?.method ?? "GET").toUpperCase();
    } catch { /* ignore */ }
    if (!GAME_URL.test(url)) return realFetch.apply(this, args);
    if (method === "POST") console.log("[ggpt] guess request seen (fetch)");
    const outgoing = method === "POST" ? rewriteGuess(args) : Promise.resolve(args);
    return outgoing.then((a) => realFetch.apply(this, a)).then(async (r) => {
      try {
        const out = processResponseText(await r.clone().text());
        if (!out) return r;
        return new W.Response(out, { status: r.status, statusText: r.statusText, headers: { "content-type": "application/json" } });
      } catch { return r; }
    });
  };

  const XHR = W.XMLHttpRequest;
  if (XHR?.prototype) {
    const origOpen = XHR.prototype.open, origSend = XHR.prototype.send;
    const dText = Object.getOwnPropertyDescriptor(XHR.prototype, "responseText");
    const dResp = Object.getOwnPropertyDescriptor(XHR.prototype, "response");
    XHR.prototype.open = function (method, url, ...rest) {
      try { this.__ggpt = { method: String(method).toUpperCase(), url: String(url) }; } catch { /* ignore */ }
      return origOpen.call(this, method, url, ...rest);
    };
    XHR.prototype.send = function (body) {
      const g = this.__ggpt;
      if (g && GAME_URL.test(g.url)) {
        try {
          if (g.method === "POST") {
            console.log("[ggpt] guess request seen (XHR)");
            if (typeof body === "string") { const nb = swapBodyText(body); if (nb) body = nb; }
            else console.log("[ggpt] guess body is not text, not touched", typeof body);
          }
          let cache;
          const patched = () => {
            if (cache === undefined) {
              const textual = this.responseType === "" || this.responseType === "text";
              const raw = textual ? dText.get.call(this) : JSON.stringify(dResp.get.call(this));
              cache = processResponseText(raw) ?? raw;
            }
            return cache;
          };
          // Read every game response as soon as it arrives, so the round data is known before the guess is made.
          this.addEventListener("readystatechange", () => { if (this.readyState === 4) { try { patched(); } catch { /* ignore */ } } });
          Object.defineProperty(this, "responseText", {
            configurable: true,
            get: () => (this.readyState === 4 ? patched() : dText.get.call(this)),
          });
          Object.defineProperty(this, "response", {
            configurable: true,
            get: () => {
              if (this.readyState !== 4) return dResp.get.call(this);
              if (this.responseType === "" || this.responseType === "text") return patched();
              if (this.responseType === "json") { try { return JSON.parse(patched()); } catch { /* fall through */ } }
              return dResp.get.call(this);
            },
          });
        } catch (e) { console.log("[ggpt] XHR hook error", e); }
      }
      return origSend.call(this, body);
    };
  }
  console.log("[ggpt] hooks installed (fetch + XHR), v0.7.2");

  setInterval(async () => {
    const token = gameToken();
    if (!token || !anyEnabled() || document.hidden) return;
    try {
      const r = await realFetch.call(W, `/api/v3/games/${token}`, { credentials: "include" });
      if (r.ok) ingest(await r.json());
    } catch { /* ignore */ }
  }, POLL_MS);

  // ── Info icon beside the round score on the results screen ────────────────
  const ICON_ATTR = "data-ggpt-info";
  const norm = (t) => String(t).replace(/[\s,.\u00a0']/g, "").toLowerCase();
  const textOk = (el, test) => { const t = norm(el.textContent || ""); return t.length > 0 && t.length < 90 && test(t); };
  function findAll(test) {
    const out = [];
    for (const el of document.body.querySelectorAll("*")) {
      if (el.children.length > 6 || /^(SCRIPT|STYLE|NOSCRIPT|SVG|PATH)$/i.test(el.tagName) || el.hasAttribute(ICON_ATTR)) continue;
      if (!textOk(el, test)) continue;
      if ([...el.children].some((c) => !c.hasAttribute(ICON_ATTR) && textOk(c, test))) continue; // keep the deepest one
      out.push(el);
    }
    return out;
  }
  // A floating dot pinned to the right of the score (no page elements are touched, so it can never end up below it).
  let info = null; // { el, anchor }
  const hideInfo = () => { if (info) { info.el.remove(); info = null; } };
  function makeInfoEl() {
    const el = document.createElement("div");
    el.setAttribute(ICON_ATTR, "1");
    el.textContent = "i";
    el.style.cssText = "position:fixed;z-index:1000;width:18px;height:18px;border-radius:50%;display:none;align-items:center;" +
      "justify-content:center;font:italic 700 12px/1 Georgia,serif;color:#e6edf3;background:rgba(22,27,34,.75);" +
      "border:1px solid rgba(230,237,243,.45);cursor:help;user-select:none";
    return el;
  }
  function placeInfoIcon() {
    if (!info) return;
    if (!info.anchor.isConnected) { info.el.style.display = "none"; return; }
    const r = info.anchor.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) { info.el.style.display = "none"; return; }
    info.el.style.display = "flex";
    info.el.style.left = `${r.right + 8}px`;
    info.el.style.top = `${r.top + r.height / 2 - 9}px`;
  }

  setInterval(() => {
    const token = gameToken();
    const all = token && shownInfo[token];
    const i = all && game && game.token === token && game.guessed ? all[game.round] : null;
    if (!i) { hideInfo(); return; }
    const tip = `Sent to server: ${i.sent.score} ${i.sent.score === 1 ? "point" : "points"} (${fmtDist(i.sent.d)} from the real spot)`;
    if (info && info.anchor.isConnected && info.round === game.round) { info.el.title = tip; return; }
    hideInfo();
    const scoreRe = new RegExp(`^\\+?${i.real.score}(points?|pts|puntos)?$`);
    const fontSize = (e) => parseFloat(getComputedStyle(e).fontSize) || 0;
    const anchor = findAll((t) => scoreRe.test(t)).sort((x, y) => fontSize(y) - fontSize(x))[0]; // the big score number
    if (!anchor) return;
    info = { el: makeInfoEl(), anchor, round: game.round };
    info.el.title = tip;
    document.documentElement.appendChild(info.el);
  }, 500);

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
    .warn{position:absolute;top:3px;right:3px;width:15px;height:15px;display:none;pointer-events:none}
    .on .warn{display:block}
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
        <svg class="warn" viewBox="0 0 24 24"><path d="M12 2.5 22.5 21h-21z" fill="#f5b301" stroke="#1b1b1b" stroke-width="1.6" stroke-linejoin="round"/><path d="M12 9v5.5" stroke="#1b1b1b" stroke-width="2.2" stroke-linecap="round"/><circle cx="12" cy="17.6" r="1.3" fill="#1b1b1b"/></svg>
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
      arrowBtn.title = st.enabled ? "Training is ON for this map" : "Training options";
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

  // Clicking Play starts the game: hide the arrow right away (the page can sit on a loading screen for a moment).
  // It comes back by itself after 10 s if we are still on the map page (e.g. the game failed to start).
  document.addEventListener("click", (e) => {
    const p = widget?.play;
    if (!p || !e.isTrusted) return;
    const path = e.composedPath();
    if (path.includes(p) || path.includes(p.closest("a") || p)) {
      widget.hiddenUntil = Date.now() + 10000;
      widget.arrowHost.style.display = "none";
      setPanel(false);
    }
  }, true);

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
    if (Date.now() < (w.hiddenUntil || 0)) { w.arrowHost.style.display = "none"; return; }
    const r = w.play.getBoundingClientRect();
    const st = w.arrowHost.style;
    if (r.width === 0 || r.height === 0) { st.display = "none"; return; }
    st.cssText = `position:fixed;z-index:1000;display:block;left:${r.right - ARROW_W}px;top:${r.top}px`;
    w.arrowBtn.style.height = `${r.height}px`;
  }
  const frame = () => { placeArrow(); placeInfoIcon(); requestAnimationFrame(frame); };
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
