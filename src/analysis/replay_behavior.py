"""replay_behavior.py — Layer 1: turn one compressed round replay into labeled behavior phases.

Pure logic, no wording. Each phase is a dict: {t, kind, key, ...facts}. `key` picks the sentence
template in replay_narrative.py; `kind` is the coarse family (useful later to attach corrective actions).
"""

import itertools
import math
from collections import Counter

from shapely.geometry import box
from shapely.ops import unary_union

# ── Tunables ────────────────────────────────────────────────────────────────
SPEED_GAP_MS = 2000  # pano changes closer than this belong to the same run
SPEED_MIN_STEPS = 3  # a run this long counts as "speed moving"
PAUSE_MS = 8000  # no action for this long (map closed) = stopped to look for clues
ZOOM_RUN_GAP_MS = 1500  # zoom events closer than this are one zoom gesture
ALIGN_MERGE_MS = 3000  # repeated align presses closer than this are one
PIN_MERGE_MS = 3000  # pins placed closer than this are one
MAP_OPEN_MIN_MS = 2000  # shorter map sessions (just confirming the guess) are not reported
VERIFY_MIN_MS = 1000  # street view time after the last pin to count as verifying
EXACT_PIN_KM = 2.0  # pin this close to the real spot = "exact"
JUMP_M = (
    300  # pano-to-pano distance above this is a teleport (back to start / checkpoint), not a step
)
START_RETURN_M = 3.0  # a pano this close to the first one = went back to start
SCAN_MIN_ZOOM = 6  # map zoom at which panning counts as searching
SCAN_MIN_SAMPLES = 5  # pan events needed to call it a search
SCAN_MAX_LOOKUPS = 40  # admin lookups per search (downsampled)
FULL_COUNTRY_REGIONS = 4  # this many regions visited = scanning the whole country
# ASSUMPTION: replays don't store the minimap pixel size, so scanned areas are estimated
# from a fixed viewport. Tune if rectangles look too big or too small.
MAP_PX = (640, 420)

_PRIORITY = {
    "start": -1,
    "map": 0,
    "zoom": 1,
    "move": 1,
    "align": 2,
    "pause": 2,
    "scan": 3,
    "pin": 4,
    "verify": 5,
    "guess": 6,
}


# ── Small helpers ───────────────────────────────────────────────────────────
def km_between(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _level(zoom: int) -> str:
    if zoom <= 3:
        return "world"
    if zoom <= 6:
        return "country"
    if zoom <= 9:
        return "region"
    if zoom <= 12:
        return "city"
    return "point"


def _viewport_bbox(lat, lng, zoom):
    deg_px = 360 / (256 * 2**zoom)
    half_lng = MAP_PX[0] / 2 * deg_px
    half_lat = MAP_PX[1] / 2 * deg_px * max(math.cos(math.radians(lat)), 0.1)
    return (max(-85, lat - half_lat), lng - half_lng, min(85, lat + half_lat), lng + half_lng)


def _label(admin) -> str:
    if not admin:
        return ""
    parts = [admin.get("city"), admin.get("state"), admin.get("country")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _short(admin, lat, lng) -> str:
    name = admin and (admin.get("city") or admin.get("state") or admin.get("country"))
    return name or f"{lat:.4f}, {lng:.4f}"


def _level_place(level, admin, lat, lng) -> str:
    if level == "world":
        return "the world view"
    if level == "point":
        return f"the point {lat:.3f}, {lng:.3f}"
    if admin:
        if level == "country" and admin.get("country"):
            return admin["country"]
        name = admin.get("state") if level == "region" else admin.get("city") or admin.get("state")
        name = name or admin.get("country")
        if name:
            return name
    return f"the area around {lat:.2f}, {lng:.2f}"


def _downsample(items, n):
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def _last_before(samples, t):
    for s in reversed(samples):
        if s[0] <= t:
            return s
    return None


# ── Event stream → raw structures ───────────────────────────────────────────
def _parse(events):
    panos, aligns, pins, sessions, guess = [], [], [], [], None
    vp = {"lat": None, "lng": None, "zoom": None}
    cur = None
    for ev in events:
        t, typ, p = ev["time"], ev["type"], ev.get("payload") or {}
        if typ == "MapPosition" and p.get("lat") is not None:
            vp["lat"], vp["lng"] = p["lat"], p["lng"]
            if cur is not None:
                cur["samples"].append((t, p["lat"], p["lng"], vp["zoom"]))
        elif typ == "MapZoom" and p.get("zoom") is not None:
            if cur is not None:
                cur["zooms"].append((t, p["zoom"], vp["zoom"]))
            vp["zoom"] = p["zoom"]
        elif typ == "MapDisplay":
            if p.get("isActive") and cur is None:
                cur = {"start": t, "end": None, "samples": [], "zooms": []}
            elif not p.get("isActive") and cur is not None:
                cur["end"] = t
                sessions.append(cur)
                cur = None
        elif typ == "PanoPosition" and p.get("lat") is not None:
            panos.append((t, p["lat"], p["lng"]))
        elif typ == "Aligning":
            aligns.append((t, dict(vp)))
        elif typ == "PinPosition" and p.get("lat") is not None:
            pins.append((t, p["lat"], p["lng"]))
        elif typ == "GuessWithLatLng" and p.get("lat") is not None:
            guess = (t, p["lat"], p["lng"])
    if cur is not None:
        cur["end"] = events[-1]["time"]
        sessions.append(cur)
    return {"panos": panos, "aligns": aligns, "pins": pins, "sessions": sessions, "guess": guess}


def _zoom_runs(zooms):
    runs = []
    for t, z, prev in zooms:
        if runs and t - runs[-1]["t1"] <= ZOOM_RUN_GAP_MS:
            runs[-1]["t1"], runs[-1]["z1"] = t, z
        else:
            runs.append({"t0": t, "t1": t, "z0": prev if prev is not None else z, "z1": z})
    return runs


# ── Main entry ──────────────────────────────────────────────────────────────
def analyze_round(events, real, result, locate) -> list[dict]:
    """events: compressed replay. real: {lat,lng,country_code,country,state,city,place}.
    result: {score, distance_km}. locate(lat, lng) -> admin dict or None (offline lookup).
    """
    if not events:
        return []
    cache: dict = {}

    def where(lat, lng):
        key = (round(lat, 3), round(lng, 3))
        if key not in cache:
            cache[key] = locate(lat, lng)
        return cache[key]

    s = _parse(events)
    phases: list[dict] = []

    def add(t, kind, key, **data) -> None:
        phases.append({"t": t, "kind": kind, "key": key, **data})

    # 1. Street view movement
    panos = s["panos"]
    if panos:
        first = panos[0]
        add(
            first[0],
            "start",
            "start",
            place=real["place"] or f"{real['lat']:.4f}, {real['lng']:.4f}",
        )
        run: list = []

        def flush() -> None:
            nonlocal run
            if len(run) >= SPEED_MIN_STEPS:
                t0, (t, lat, lng) = run[0][0], run[-1]
                add(
                    t0,
                    "move",
                    "speed_move",
                    place=_short(where(lat, lng), lat, lng),
                    steps=len(run),
                    dur_ms=t - t0,
                    lat=lat,
                    lng=lng,
                )
            else:
                for t, lat, lng in run:
                    add(
                        t, "move", "move", place=_short(where(lat, lng), lat, lng), lat=lat, lng=lng
                    )
            run = []

        for pano in panos[1:]:
            t, lat, lng = pano
            if km_between(lat, lng, first[1], first[2]) * 1000 <= START_RETURN_M:
                flush()
                add(t, "move", "return_start")
                continue
            if run and t - run[-1][0] > SPEED_GAP_MS:
                flush()
            run.append(pano)
        flush()

    # 2. Map sessions: opening, zooming, searching
    for sess in s["sessions"]:
        if sess["end"] - sess["start"] >= MAP_OPEN_MIN_MS:
            add(sess["start"], "map", "map_open", dur_ms=sess["end"] - sess["start"])
        runs = _zoom_runs(sess["zooms"])
        for r in runs:
            if r["z1"] == r["z0"]:
                continue
            c = _last_before(sess["samples"], r["t1"])
            level = _level(r["z1"])
            admin = where(c[1], c[2]) if c else None
            place = _level_place(level, admin, *(c[1:3] if c else (0, 0))) if c else "the map"
            add(
                r["t0"],
                "zoom",
                "zoom_in" if r["z1"] > r["z0"] else "zoom_out",
                place=place,
                level=level,
            )
        windows = [(r["t0"], r["t1"]) for r in runs]
        pans = [
            p
            for p in sess["samples"]
            if p[3] is not None
            and p[3] >= SCAN_MIN_ZOOM
            and not any(a <= p[0] <= b for a, b in windows)
        ]
        if len(pans) >= SCAN_MIN_SAMPLES:
            add(pans[0][0], "scan", **_scan(pans, real, where))

    # 3. Aligning
    last = -(10**9)
    for t, vp in s["aligns"]:
        merged = t - last <= ALIGN_MERGE_MS
        last = t
        if merged:
            continue
        key, place = "align", ""
        zoom = vp["zoom"] or 0
        if vp["lat"] is not None and zoom >= 7:
            a = where(vp["lat"], vp["lng"])
            if a and a.get("country_code") == real["country_code"]:
                if zoom >= 10 and real["city"] and a.get("city") == real["city"]:
                    key, place = "align_city", real["city"]
                elif real["state"] and a.get("state") == real["state"]:
                    key, place = "align_region", real["state"]
        add(t, "align", key, place=place)

    # 4. Pauses in street view
    acts = {t for t, _, _ in panos} | {t for t, _ in s["aligns"]} | {t for t, _, _ in s["pins"]}
    acts |= {x["start"] for x in s["sessions"]} | {x["end"] for x in s["sessions"]}
    if s["guess"]:
        acts.add(s["guess"][0])
    ordered = sorted(acts)
    for a, b in itertools.pairwise(ordered):
        if b - a >= PAUSE_MS and not any(x["start"] <= a < x["end"] for x in s["sessions"]):
            add(a, "pause", "pause", dur_ms=b - a)

    # 5. Pins, verification, guess
    pins: list = []
    for t, lat, lng in s["pins"]:
        if pins and t - pins[-1][0] <= PIN_MERGE_MS:
            pins[-1] = (t, lat, lng)
        else:
            pins.append((t, lat, lng))
    for t, lat, lng in pins:
        d = km_between(lat, lng, real["lat"], real["lng"])
        a = where(lat, lng)
        if d <= EXACT_PIN_KM:
            cls = "exact"
        elif (
            a
            and a.get("country_code")
            and real["country_code"]
            and a["country_code"] != real["country_code"]
        ):
            cls = "wrong_country"
        elif a and real["state"] and a.get("state") and a["state"] != real["state"]:
            cls = "wrong_region"
        elif a:
            cls = "right_region"
        else:
            cls = "off"
        add(
            t,
            "pin",
            f"pin_{cls}",
            place=_label(a) or f"{lat:.3f}, {lng:.3f}",
            dist_km=d,
            lat=lat,
            lng=lng,
        )

    g = s["guess"]
    if pins and g:
        pin_t = pins[-1][0]
        back = [t for t, _, _ in panos if pin_t < t < g[0]] + [
            t for t, _ in s["aligns"] if pin_t < t < g[0]
        ]
        if back and g[0] - pin_t >= VERIFY_MIN_MS:
            exact = phases and any(p["key"] == "pin_exact" and p["t"] == pin_t for p in phases)
            add(pin_t, "verify", "verify_exact" if exact else "verify_other", dur_ms=g[0] - pin_t)
    if g:
        add(g[0], "guess", "guess", score=result["score"], dist_km=result["distance_km"])
    else:
        add(events[-1]["time"], "guess", "no_guess")

    phases.sort(key=lambda p: (p["t"], _PRIORITY.get(p["kind"], 5)))
    return phases


def _scan(pans, real, where) -> dict:
    regions: Counter = Counter()
    countries: Counter = Counter()
    for _, lat, lng, _ in _downsample(pans, SCAN_MAX_LOOKUPS):
        a = where(lat, lng)
        if a:
            countries[a.get("country_code"), a.get("country")] += 1
            regions[a.get("country_code"), a.get("state") or a.get("country")] += 1
    boxes = [tuple(round(v, 4) for v in _viewport_bbox(lat, lng, z)) for _, lat, lng, z in pans]
    base = {
        "dur_ms": pans[-1][0] - pans[0][0],
        "boxes": boxes,
        "real_place": real["place"],
        "n_regions": len(regions),
        "place": "",
    }
    if len(countries) >= 2:
        base.update(
            key="scan_multi_country",
            place=", ".join(n for (_, n), _ in countries.most_common(3) if n),
        )
    elif len(regions) >= FULL_COUNTRY_REGIONS:
        (code, name), _ = countries.most_common(1)[0]
        base.update(
            key="scan_country" if code == real["country_code"] else "scan_country_wrong", place=name
        )
    elif regions:
        top = [k for k, _ in regions.most_common(3)]
        want = real["state"] or real["country"]
        hit = any(c == real["country_code"] and n == want for c, n in top)
        base.update(
            key="scan_regions_hit" if hit else "scan_regions_miss",
            place=", ".join(n for _, n in top),
        )
    else:
        mid = pans[len(pans) // 2]
        base.update(key="scan_area", place=f"{mid[1]:.2f}, {mid[2]:.2f}")
    return base


def build_path(events) -> dict:
    """Street view path: solid segments for walking, jumps for teleports (back to start, checkpoint)."""
    segments, jumps, cur, prev = [], [], [], None
    for _, lat, lng in _parse(events)["panos"] if events else []:
        pt = [round(lat, 6), round(lng, 6)]
        if prev is not None:
            if pt == prev:
                continue
            if km_between(prev[0], prev[1], pt[0], pt[1]) * 1000 > JUMP_M:
                segments.append(cur)
                jumps.append([prev, pt])
                cur = []
        cur.append(pt)
        prev = pt
    if cur:
        segments.append(cur)
    return {"segments": [seg for seg in segments if len(seg) > 1], "jumps": jumps}


def searched_polygons(phases) -> list:
    """Everything the user looked at on the map, merged into one shape.
    Returns polygons as [[ring, ...], ...] with rings of [lat, lng] (Leaflet order).
    """
    boxes = [box(w, s, e, n) for p in phases for (s, w, n, e) in p.get("boxes", [])]
    if not boxes:
        return []
    merged = unary_union(boxes).simplify(0.002)
    polys = [merged] if merged.geom_type == "Polygon" else list(merged.geoms)
    return [
        [[[y, x] for x, y in ring.coords] for ring in (pg.exterior, *pg.interiors)] for pg in polys
    ]
