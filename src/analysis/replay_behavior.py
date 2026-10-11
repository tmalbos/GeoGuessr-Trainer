"""replay_behavior.py — Layer 1: turn one compressed round replay into labeled behavior phases.

Pure logic, no wording. Each phase is a dict: {t, kind, key, ...facts}. `key` picks the sentence
template in replay_narrative.py; `kind` is the coarse family. Grades are NOT decided here
(see replay_grading.py), but the facts the grader needs (speed ratios, revisit counts, views) are.
"""

import bisect
import itertools
import math
from collections import Counter

import shapely
from shapely.geometry import Point, box
from shapely.ops import unary_union

from src.analysis.scoring import dist_to_score

# ── Tunables ────────────────────────────────────────────────────────────────
# Street view movement
SPEED_GAP_MS = 900  # steps closer than this (every one of them) = "speed moving"
SPEED_MIN_STEPS = 3  # a run this long can count as "speed moving"
MOVE_RUN_GAP_MS = 6000  # steps closer than this (and nothing else in between) are one run
STEP_EXPECTED_S = 0.9  # fast-moving pace: expected seconds per step (run time = first→last step)
REVISITS = 3  # standing on the same pano this many times is wasteful, always
REVISIT_SLOW_MS = 5000  # ...but ONE return already counts if the previous visit was this long ago
BACK_ANGLE = 120  # a single step turning more than this from the last run is "moving back"
PAUSE_MS = 8000  # no action for this long (map closed) = stopped to look for clues
JUMP_M = (
    300  # pano-to-pano distance above this is a teleport (back to start / checkpoint), not a step
)
START_RETURN_M = 3.0  # a pano this close to the first one = went back to start

# Map
ZOOM_RUN_GAP_MS = 1500  # zoom events closer than this are one zoom gesture
ZOOM_STEP_S = 0.275  # expected seconds per zoom level when zooming in properly
ZOOM_SLOW_RATIO = 1.5  # a zoom taking this many times the expected time is "slow"
OPEN_ZOOM_MS = 1500  # a zoom starting this soon after opening the map belongs to the opening
STARE_EXPECTED_S = 10.0  # staring at the same zoom level for less than this is fine
STARE_IDLE_GAP_MS = 2000  # a silence this long inside a map session counts as idle
STARE_IDLE_SHARE = 0.5  # a stretch is a "stare" when at least this share of it was idle
QUICK_MAP_MS = 3000  # open+close faster than this, with no pin and a ≤1 level zoom = wasted
CLOSE_MERGE_MS = 6000  # "closed the map" folds into a step/align starting this soon after
MAP_OPEN_MIN_MS = 2000  # a session doing nothing at all is only reported if it lasted this long
SCAN_MIN_ZOOM = 6  # map zoom at which panning counts as searching
SCAN_MIN_SAMPLES = 5  # pan events needed to call it a search
SCAN_MAX_LOOKUPS = 40  # admin lookups per search (downsampled)
SCAN_MAX_BOXES = 400  # viewport boxes kept per search (downsampled) before clipping
FULL_COUNTRY_REGIONS = 4  # this many regions visited = scanning the whole country
# How much of the country a map window covers (smaller side, vs the country's bounding box)
FRAME_COUNTRY = 0.6  # at least this much = the view frames the whole country
FRAME_REGION = 0.1  # at least this much = a region of it
FRAME_CITY = 0.004  # at least this much = a city/close area, below that a point
FRAME_WIDE = 2.5  # more than this = the view is much bigger than the country

# Other
ALIGN_MERGE_MS = 3000  # repeated align presses closer than this are one
ALIGN_OPEN_MS = 4000  # the map opening this soon after an align belongs to that align
PIN_MERGE_MS = 3000  # pins placed closer than this are one
VERIFY_MIN_MS = 1000  # street view time after the last pin to count as verifying
EXACT_PIN_KM = 0.025  # pin this close to the real spot = "exact" (25 m)

# The minimap is a standard Web Mercator map (256 px tiles): the visible window is the viewport
# in pixels converted back to degrees at the current zoom, so each zoom halves it.
# MAP_PX was measured in-game: at every zoom from 3 to 9 the visible extent matches 1245x775 px.
MAP_PX = (1245, 775)
TILE_PX = 256
MAX_LAT = 85.0511  # Web Mercator limit

# A viewport box is clipped to the country that covers most of it, but only if that country
# covers at least this share of the box; otherwise (sea, uncovered country) the plain box is kept.
CLIP_MIN_SHARE = 0.25

_PRIORITY = {
    "start": -1,
    "map": 0,
    "zoom": 1,
    "move": 1,
    "align": 2,
    "pause": 2,
    "scan": 3,
    "stare": 3,
    "pin": 4,
    "verify": 5,
    "guess": 6,
}

COMPASS = (
    "north",
    "northeast",
    "east",
    "southeast",
    "south",
    "southwest",
    "west",
    "northwest",
)


# ── Small helpers ───────────────────────────────────────────────────────────
def km_between(lat1, lng1, lat2, lng2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def _bearing(lat1, lng1, lat2, lng2) -> float:
    """Compass bearing in degrees (0 = north, 90 = east) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _compass(deg: float) -> str:
    return COMPASS[int((deg + 22.5) // 45) % 8]


def _angle_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def _pano_key(p) -> tuple[float, float]:
    return (round(p[1], 5), round(p[2], 5))


def _level(zoom: int) -> str:
    """Fallback level from the zoom number alone (used when the country's size is unknown)."""
    if zoom <= 3:
        return "world"
    if zoom <= 4:
        return "wide"
    if zoom <= 6:
        return "country"
    if zoom <= 9:
        return "region"
    if zoom <= 12:
        return "city"
    return "point"


def _merc_y(lat: float, world_px: float) -> float:
    lat = max(-MAX_LAT, min(MAX_LAT, lat))
    return (
        0.5 - math.log(math.tan(math.pi / 4 + math.radians(lat) / 2)) / (2 * math.pi)
    ) * world_px


def _merc_lat(y: float, world_px: float) -> float:
    return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / world_px))))


def _viewport_bbox(lat, lng, zoom) -> tuple[float, float, float, float]:
    """(west, south, east, north) in degrees of the map window centered on (lat, lng) at `zoom`."""
    world_px = TILE_PX * 2**zoom
    cx = (lng + 180) / 360 * world_px
    cy = _merc_y(lat, world_px)
    half_w, half_h = MAP_PX[0] / 2, MAP_PX[1] / 2
    west = (cx - half_w) / world_px * 360 - 180
    east = (cx + half_w) / world_px * 360 - 180
    north = _merc_lat(max(0.0, cy - half_h), world_px)
    south = _merc_lat(min(world_px, cy + half_h), world_px)
    return (max(-180.0, west), south, min(180.0, east), north)


def _label(admin) -> str:
    if not admin:
        return ""
    parts = [admin.get("city"), admin.get("state"), admin.get("country")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _pin_place(admin, with_country: bool) -> str:
    """City + state; the country only when the pin is in another one."""
    if not admin:
        return ""
    parts = [admin.get("city"), admin.get("state")]
    if with_country:
        parts.append(admin.get("country"))
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _short(admin, lat, lng) -> str:
    name = admin and (admin.get("city") or admin.get("state") or admin.get("country"))
    return name or f"{lat:.4f}, {lng:.4f}"


def _view_place(level, admin, lat, lng) -> str:
    """Name of what a map window shows, at the level it frames."""
    if level == "world":
        return "the world view"
    if level == "wide":
        country = admin and admin.get("country")
        return f"a wide area around {country}" if country else "a wide area"
    if level == "point":
        return f"the point {lat:.3f}, {lng:.3f}"
    if admin:
        if level == "country":
            name = admin.get("country")
        elif level == "region":
            name = admin.get("state") or admin.get("country")
        else:
            name = admin.get("city") or admin.get("state") or admin.get("country")
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


def _country_extent(countries_in, lat, lng):
    """(code, width°, height°) of the bounding box of the country piece under the point, or None."""
    if countries_in is None:
        return None
    try:
        candidates = countries_in(lng - 0.02, lat - 0.02, lng + 0.02, lat + 0.02)
    except Exception:  # noqa: BLE001
        return None
    pt = Point(lng, lat)
    for code, geom in candidates:
        try:
            parts = list(geom.geoms) if hasattr(geom, "geoms") else [geom]
            part = next((g for g in parts if g.intersects(pt)), None)
        except Exception:  # noqa: BLE001
            continue
        if part is not None:
            w, s, e, n = part.bounds
            return code, max(e - w, 1e-6), max(n - s, 1e-6)
    return None


def _view_level(bbox, zoom, extent) -> str:
    """What a map window frames: world / wide / country / region / city / point.

    With the country's size known, it is decided by how much of the country the window covers
    (a small country is framed at a higher zoom than a big one). Otherwise by zoom alone.
    """
    if zoom <= 3:
        return "world"
    if not extent:
        return _level(zoom)
    _, cw, ch = extent
    share = min((bbox[2] - bbox[0]) / cw, (bbox[3] - bbox[1]) / ch)
    if share > FRAME_WIDE:
        return _level(zoom) if zoom > 4 else "wide"
    if share >= FRAME_COUNTRY:
        return "country"
    if share >= FRAME_REGION:
        return "region"
    if share >= FRAME_CITY:
        return "city"
    return "point"


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
                cur = {
                    "start": t,
                    "end": None,
                    "closed": False,
                    "samples": [],
                    "zooms": [],
                    "open_view": dict(vp),  # where the map was when it was opened
                }
            elif not p.get("isActive") and cur is not None:
                cur["end"] = t
                cur["closed"] = True
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


def _is_5k(km: float) -> bool:
    return dist_to_score(km) >= 5000


# ── Main entry ──────────────────────────────────────────────────────────────
def analyze_round(events, real, result, locate, countries_in=None) -> list[dict]:
    """events: compressed replay. real: {lat,lng,country_code,country,state,city,place}.
    result: {score, distance_km}. locate(lat, lng) -> admin dict or None (offline lookup).
    countries_in(west, south, east, north) -> [(code, shapely geometry)] of the countries that
    touch that box (offline lookup); used to clip searched areas to land and to tell when a map
    window frames a whole country. Optional.
    """
    if not events:
        return []
    cache: dict = {}
    ext_cache: dict = {}

    def where(lat, lng):
        key = (round(lat, 3), round(lng, 3))
        if key not in cache:
            cache[key] = locate(lat, lng)
        return cache[key]

    def extent(lat, lng):
        key = (round(lat, 2), round(lng, 2))
        if key not in ext_cache:
            ext_cache[key] = _country_extent(countries_in, lat, lng)
        return ext_cache[key]

    s = _parse(events)
    phases: list[dict] = []

    def add(t, kind, key, **data) -> None:
        phases.append({"t": t, "kind": kind, "key": key, **data})

    panos, sessions, guess = s["panos"], s["sessions"], s["guess"]

    # Pins closer together than PIN_MERGE_MS are one
    pins: list = []
    for t, lat, lng in s["pins"]:
        if pins and t - pins[-1][0] <= PIN_MERGE_MS:
            pins[-1] = (t, lat, lng)
        else:
            pins.append((t, lat, lng))

    # 1. Street view movement ─────────────────────────────────────────────────
    if panos:
        first = panos[0]
        add(
            first[0],
            "start",
            "start",
            place=real["place"] or f"{real['lat']:.4f}, {real['lng']:.4f}",
        )
        _movement(panos, s, guess, add)

    # 2. Map sessions: zooming, staring, searching ────────────────────────────
    for sess in sessions:
        _map_session(sess, s, pins, guess, real, add, where, extent, countries_in)

    # 3. Aligning
    last = -(10**9)
    nth_at: Counter = Counter()
    for t, vp in s["aligns"]:
        merged = t - last <= ALIGN_MERGE_MS
        last = t
        if merged:
            continue
        pano = _last_before(panos, t)
        at_spawn = bool(
            pano and km_between(pano[1], pano[2], panos[0][1], panos[0][2]) * 1000 <= START_RETURN_M
        )
        spot = "spawn" if at_spawn else (_pano_key(pano) if pano else None)
        nth_at[spot] += 1
        nth = nth_at[spot]
        key, place = "align", ""
        zoom = vp["zoom"] or 0
        if not at_spawn and nth == 1 and vp["lat"] is not None and zoom >= 7:
            a = where(vp["lat"], vp["lng"])
            if a and a.get("country_code") == real["country_code"]:
                if zoom >= 10 and real["city"] and a.get("city") == real["city"]:
                    key, place = "align_city", real["city"]
                elif real["state"] and a.get("state") == real["state"]:
                    key, place = "align_region", real["state"]
        then_open = any(0 < x["start"] - t <= ALIGN_OPEN_MS for x in sessions)
        add(t, "align", key, place=place, nth=nth, at_spawn=at_spawn, then_open=then_open)

    # 4. Pauses in street view
    acts = {t for t, _, _ in panos} | {t for t, _ in s["aligns"]} | {t for t, _, _ in s["pins"]}
    acts |= {x["start"] for x in sessions} | {x["end"] for x in sessions}
    if guess:
        acts.add(guess[0])
    ordered = sorted(acts)
    for a, b in itertools.pairwise(ordered):
        if b - a >= PAUSE_MS and not any(x["start"] <= a < x["end"] for x in sessions):
            add(a, "pause", "pause", dur_ms=b - a)

    # 5. Pins
    prev_d = None
    for t, lat, lng in pins:
        d = km_between(lat, lng, real["lat"], real["lng"])
        a = where(lat, lng)
        wrong_country = bool(
            a
            and a.get("country_code")
            and real["country_code"]
            and a["country_code"] != real["country_code"]
        )
        if d <= EXACT_PIN_KM:
            cls = "exact"
        elif _is_5k(d):
            cls = "5k"
        elif wrong_country:
            cls = "wrong_country"
        elif a and real["state"] and a.get("state") and a["state"] != real["state"]:
            cls = "wrong_region"
        elif a:
            cls = "right_region"
        else:
            cls = "off"
        place = (_label(a) if cls == "exact" else _pin_place(a, wrong_country)) or (
            f"{lat:.3f}, {lng:.3f}"
        )
        add(
            t,
            "pin",
            f"pin_{cls}",
            place=place,
            dist_km=d,
            prev_dist_km=prev_d,
            lat=lat,
            lng=lng,
        )
        prev_d = d

    # 6. Verification and guess
    if pins and guess:
        pin_t = pins[-1][0]
        # Steps taken after the last pin are the player validating it.
        for p in phases:
            if p["kind"] == "move" and p["key"] in {"move", "run", "speed_move"}:
                if pin_t < p["t"] < guess[0]:
                    p["key"] = "verify_move"
        has_verify_move = any(p["key"] == "verify_move" for p in phases)
        back = [t for t, _, _ in panos if pin_t < t < guess[0]] + [
            t for t, _ in s["aligns"] if pin_t < t < guess[0]
        ]
        if back and guess[0] - pin_t >= VERIFY_MIN_MS and not has_verify_move:
            exact = any(p["key"] in {"pin_exact", "pin_5k"} and p["t"] == pin_t for p in phases)
            add(
                pin_t,
                "verify",
                "verify_exact" if exact else "verify_other",
                dur_ms=guess[0] - pin_t,
            )
    if guess:
        add(guess[0], "guess", "guess", score=result["score"], dist_km=result["distance_km"])
    else:
        add(events[-1]["time"], "guess", "no_guess")

    phases.sort(key=lambda p: (p["t"], _PRIORITY.get(p["kind"], 5)))
    return _fold_map_close(phases)


def _fold_map_close(phases: list[dict]) -> list[dict]:
    """ "Closed the map" is folded into the single step / align that follows it right away.
    Alone it is only kept for map sessions where nothing was pinned.
    """
    out = []
    for i, p in enumerate(phases):
        if p["key"] != "map_close":
            out.append(p)
            continue
        nxt = phases[i + 1] if i + 1 < len(phases) else None
        if (
            nxt
            and nxt["t"] - p["t"] <= CLOSE_MERGE_MS
            and (nxt["key"] == "move" or nxt["kind"] == "align")
        ):
            nxt["after_close"] = True
            continue
        if p["show"]:
            out.append(p)
    return out


# ── Street view movement ────────────────────────────────────────────────────
def _movement(panos, s, guess, add) -> None:
    """Group steps into runs and describe each: heading, speed, revisits, going back to spawn."""
    first = panos[0]
    cuts = sorted(
        {x["start"] for x in s["sessions"]}
        | {x["end"] for x in s["sessions"]}
        | {t for t, _ in s["aligns"]}
        | {t for t, _, _ in s["pins"]}
        | ({guess[0]} if guess else set()),
    )

    def acted_between(a, b) -> bool:
        i = bisect.bisect_right(cuts, a)
        return i < len(cuts) and cuts[i] < b

    visits: dict = {_pano_key(first): [first[0]]}
    state = {"runs": 0, "bearing": None}
    cur: dict | None = None
    prev_pano = first
    spawn_visits = 1

    def flush() -> None:
        nonlocal cur
        if not cur:
            return
        items, prev = cur["items"], cur["prev"]
        n, last = len(items), items[-1]
        bearing = _bearing(prev[1], prev[2], last[1], last[2])
        direction = _compass(bearing)
        wasteful = sum(1 for it in items if it[3])
        span = last[0] - items[0][0]
        common = {
            "steps": n,
            "direction": direction,
            "lat": last[1],
            "lng": last[2],
            "dur_ms": span,
        }
        t0 = items[0][0]
        if wasteful >= 2:
            loop = _pano_key(last) == _pano_key(prev)
            add(t0, "move", "erratic", wasteful=wasteful, loop=loop, **common)
        elif n == 1 and wasteful == 1:
            add(t0, "move", "move_revisit", **common)
        elif n == 1:
            back = (
                state["bearing"] is not None and _angle_diff(bearing, state["bearing"]) > BACK_ANGLE
            )
            add(t0, "move", "move", back=back, **common)
        else:
            expected = STEP_EXPECTED_S * 1000 * n
            gaps = [b[0] - a[0] for a, b in itertools.pairwise(items)]
            speed = n >= SPEED_MIN_STEPS and max(gaps) <= SPEED_GAP_MS
            add(
                t0,
                "move",
                "speed_move" if speed else "run",
                started=state["runs"] == 0,
                expected_ms=expected,
                ratio=span / expected,
                **common,
            )
        state["runs"] += 1
        state["bearing"] = bearing
        cur = None

    for pano in panos[1:]:
        t, lat, lng = pano
        if km_between(lat, lng, first[1], first[2]) * 1000 <= START_RETURN_M:
            flush()
            spawn_visits += 1
            add(t, "move", "return_start", visit=spawn_visits)
            prev_pano = pano
            continue
        seen = visits.setdefault(_pano_key(pano), [])
        k = len(seen) + 1
        wasteful = k >= REVISITS or (k >= 2 and t - seen[-1] > REVISIT_SLOW_MS)
        seen.append(t)
        if cur and (
            t - cur["items"][-1][0] > MOVE_RUN_GAP_MS or acted_between(cur["items"][-1][0], t)
        ):
            flush()
        if cur is None:
            cur = {"prev": prev_pano, "items": []}
        cur["items"].append((t, lat, lng, wasteful))
        prev_pano = pano
    flush()


# ── Map sessions ────────────────────────────────────────────────────────────
def _map_session(sess, s, pins, guess, real, add, where, extent, countries_in) -> None:
    start, end = sess["start"], sess["end"]
    dur = end - start
    runs = _zoom_runs(sess["zooms"])
    pins_in = [p for p in s["pins"] if start <= p[0] <= end]
    guess_in = bool(guess and start <= guess[0] <= end)
    quiet = not pins_in and not guess_in

    # Opened and closed in a blink without doing anything useful
    if (
        sess["closed"]
        and quiet
        and dur < QUICK_MAP_MS
        and len(runs) <= 1
        and all(abs(r["z1"] - r["z0"]) <= 1 for r in runs)
    ):
        zdir = ""
        if runs and runs[0]["z1"] != runs[0]["z0"]:
            zdir = "in" if runs[0]["z1"] > runs[0]["z0"] else "out"
        add(start, "map", "quick_map", zoom_dir=zdir, dur_ms=dur)
        return

    open_view = sess["open_view"]
    emitted = False

    def view_at(c, zoom):
        """(place, level, view dict) of the map window centered on sample c at `zoom`."""
        bx = _viewport_bbox(c[1], c[2], zoom)
        level = _view_level(bx, zoom, extent(c[1], c[2]))
        place = _view_place(level, where(c[1], c[2]), c[1], c[2])
        return (
            place,
            level,
            {
                "view_boxes": [bx],
                "zoom": zoom,
                "level": level,
                "center": (c[1], c[2]),
            },
        )

    # Zoom gestures
    for i, r in enumerate(runs):
        if r["z1"] == r["z0"]:
            continue
        c = _last_before(sess["samples"], r["t1"])
        if c is None and open_view["lat"] is not None:
            c = (start, open_view["lat"], open_view["lng"], open_view["zoom"])
        zin = r["z1"] > r["z0"]
        place, level, view = view_at(c, r["z1"]) if c else ("the map", "", {})
        if zin:
            expected = ZOOM_STEP_S * 1000 * (r["z1"] - r["z0"] + 1)
            actual = r["t1"] - r["t0"]
            ratio = actual / expected
        else:
            expected = actual = ratio = 0
        if zin and ratio >= ZOOM_SLOW_RATIO and c:
            add(
                r["t0"],
                "zoom",
                "zoom_start",
                place=place,
                level=level,
                ratio=ratio,
                expected_ms=expected,
                dur_ms=actual,
                opened=i == 0 and r["t0"] - start <= OPEN_ZOOM_MS,
            )
            add(r["t1"], "zoom", "zoom_end", place=place, **view)
        else:
            add(r["t0"], "zoom", "zoom_in" if zin else "zoom_out", place=place, **view)
        emitted = True

    # Staring: long stretches at one zoom level where almost nothing happens
    windows = [(r["t0"], r["t1"]) for r in runs]
    bounds, prev_end = [], start
    for a, b in windows:
        bounds.append((prev_end, a))
        prev_end = b
    bounds.append((prev_end, end))
    stare_spans = []
    for a, b in bounds:
        total = b - a
        if total < STARE_EXPECTED_S * 1000:
            continue
        stamps = [a, *sorted(x[0] for x in sess["samples"] if a < x[0] < b), b]
        idle = sum(y - x for x, y in itertools.pairwise(stamps) if y - x >= STARE_IDLE_GAP_MS)
        if idle < STARE_IDLE_SHARE * total:
            continue
        zoom = next((r["z1"] for r in reversed(runs) if r["t1"] <= a), open_view["zoom"])
        c = _last_before(sess["samples"], b)
        if c is None and open_view["lat"] is not None:
            c = (start, open_view["lat"], open_view["lng"], zoom)
        if zoom is None or c is None:
            continue
        place, level, _ = view_at(c, zoom)
        data = {"place": place, "level": level, "ratio": total / (STARE_EXPECTED_S * 1000)}
        inside = [
            x
            for x in sess["samples"]
            if a <= x[0] <= b and x[3] is not None and x[3] >= SCAN_MIN_ZOOM
        ]
        boxes = list(
            dict.fromkeys(
                tuple(round(v, 4) for v in _viewport_bbox(x[1], x[2], x[3])) for x in inside
            ),
        )
        if boxes:
            data["areas"] = _clip_boxes(_downsample(boxes, SCAN_MAX_BOXES), countries_in)
        add(a, "stare", "stare", dur_ms=total, **data)
        stare_spans.append((a, b))
        emitted = True

    # Searching: panning around at a useful zoom
    pans = [
        p
        for p in sess["samples"]
        if p[3] is not None
        and p[3] >= SCAN_MIN_ZOOM
        and not any(a <= p[0] <= b for a, b in windows)
        and not any(a <= p[0] <= b for a, b in stare_spans)
    ]
    if len(pans) >= SCAN_MIN_SAMPLES:
        add(pans[0][0], "scan", **_scan(pans, real, where, countries_in))
        emitted = True

    # A session that did nothing we can name
    if not emitted and quiet and dur >= MAP_OPEN_MIN_MS:
        add(start, "map", "map_open", dur_ms=dur)
        return
    if sess["closed"]:
        add(end, "map", "map_close", show=quiet)


def _safe_intersection(a, geom):
    try:
        return a.intersection(geom)
    except Exception:  # noqa: BLE001
        try:
            return a.intersection(shapely.make_valid(geom))
        except Exception:  # noqa: BLE001
            return a


def _overlap_area(geom, b) -> float:
    try:
        return float(shapely.clip_by_rect(geom, *b).area)
    except Exception:  # noqa: BLE001
        return 0.0


def _clip_boxes(boxes, countries_in) -> list:
    """Viewport boxes (west, south, east, north) -> shapes of where the user was probably looking.

    Each box is assigned to the country covering most of it and clipped to that country's outline.
    Boxes with no clear country (sea, country without local data) stay as plain rectangles.
    """
    if countries_in is None:
        return list(itertools.starmap(box, boxes))
    by_country: dict = {}
    loose: list = []
    for b in boxes:
        rect = box(*b)
        best_code, best_geom, best_area = None, None, 0.0
        try:
            candidates = countries_in(*b)
        except Exception:  # noqa: BLE001
            candidates = []
        for code, geom in candidates:
            area = _overlap_area(geom, b)
            if area > best_area:
                best_code, best_geom, best_area = code, geom, area
        if best_code is not None and best_area >= CLIP_MIN_SHARE * rect.area:
            by_country.setdefault(best_code, (best_geom, []))[1].append(rect)
        else:
            loose.append(rect)
    out = list(loose)
    for geom, rects in by_country.values():
        out.append(_safe_intersection(unary_union(rects), geom))
    return out


def _scan(pans, real, where, countries_in=None) -> dict:
    regions: Counter = Counter()
    countries: Counter = Counter()
    for _, lat, lng, _ in _downsample(pans, SCAN_MAX_LOOKUPS):
        a = where(lat, lng)
        if a:
            countries[a.get("country_code"), a.get("country")] += 1
            regions[a.get("country_code"), a.get("state") or a.get("country")] += 1
    boxes = list(
        dict.fromkeys(
            tuple(round(v, 4) for v in _viewport_bbox(lat, lng, z)) for _, lat, lng, z in pans
        ),
    )
    base = {
        "dur_ms": pans[-1][0] - pans[0][0],
        "areas": _clip_boxes(_downsample(boxes, SCAN_MAX_BOXES), countries_in),
        "real_place": real["place"],
        "n_regions": len(regions),
        "place": "",
        # Every map window seen during the search (west, south, east, north), for grading.
        "view_boxes": boxes,
        "zoom": max(z for _, _, _, z in pans),
        "center": (pans[len(pans) // 2][1], pans[len(pans) // 2][2]),
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


def _polygons(geom) -> list:
    if geom is None or geom.is_empty:
        return []
    if geom.geom_type == "Polygon":
        return [geom]
    if hasattr(geom, "geoms"):
        return [pg for g in geom.geoms for pg in _polygons(g)]
    return []


def searched_polygons(phases) -> list:
    """Everything the user looked at on the map, merged into one shape (clipped to land where known).
    Returns polygons as [[ring, ...], ...] with rings of [lat, lng] (Leaflet order).
    """
    areas = [a for p in phases for a in p.get("areas", []) if a is not None and not a.is_empty]
    if not areas:
        return []
    merged = unary_union(areas).simplify(0.002)
    return [
        [[[y, x] for x, y in ring.coords] for ring in (pg.exterior, *pg.interiors)]
        for pg in _polygons(merged)
    ]
