"""replay_look.py — Street-view "looking" behavior: staring, zooming in, looking around.

Reads the PanoPov / PanoZoom events of a replay (stored uncollapsed since the full-replay change).
Old replays that only have "Deliberating" markers simply produce no look events here.

The round is cut into SEGMENTS. A new segment starts at every PanoPosition (a step), every map
open/close and every align press, so nothing is accumulated across them. Inside one segment:

  * ZOOM LOOK   zoom >= ZOOM_IN_MIN for at least LOOK_MIN_MS      -> "You zoomed into this for Xs"
  * STARE       zoom <  ZOOM_IN_MIN, camera barely moves
                (within STARE_DRIFT_DEG) for >= LOOK_MIN_MS        -> "You stared at this for Xs"
  * SWEEP       heading coverage >= SWEEP_MIN_DEG and no zoom look -> "You (really) patiently looked around"
  * if there is a sweep AND a zoom look, the first zoom look gets "while looking around..."

  * OPEN LOOK   the map opened right after a zoomed-in view   -> "You saw this and immediately opened the map"

A zoom look is never "averaged": the view it shows is the zoomed-OUT view that still contains
everything seen while zoomed in. If that needs a zoom below ZOOM_IN_MIN, no event is generated.

Each look carries a `view` ({lat, lng, heading, pitch, fov}) that the UI renders as a Street View photo.
Everything you may want to tune is in the block below.
"""

import itertools
import math

# ── Tunables ────────────────────────────────────────────────────────────────
LOOK_MIN_MS = 2500  # minimum time for a stare / zoom look to be reported
ZOOM_IN_MIN = (
    0.9  # zoom (GeoGuessr scale, 0 = default, ~3 = max) at which it counts as "zoomed in".
)
#                    Below: stares. At or above: zoom looks.
STARE_DRIFT_DEG = 12.0  # how far the camera may wander and still be "staying in place"
SWEEP_MIN_DEG = 240.0  # heading coverage needed to call it "looking around"
SWEEP_EXPECTED_MS = 5000  # expected duration of a sweep
SWEEP_QUICK_RATIO = 0.5  # faster than this x expected -> "quick glance"
SWEEP_PATIENT_RATIO = 2.0  # at least this x expected -> "patiently"
SWEEP_REALLY_PATIENT_RATIO = 3.0  # at least this x expected -> "really patiently"
OPEN_AFTER_LOOK_MS = 1500  # map opened this soon after the last PanoPov/PanoZoom (while zoomed) -> "saw this and immediately opened the map"

# Views
FOV_AT_ZOOM_0 = 90.0  # horizontal field of view at zoom 0; each zoom level halves it
VIEW_FOV_MIN, VIEW_FOV_MAX = 10.0, 100.0  # fov range kept in a view
VIEW_ASPECT = 16 / 9  # width / height of the street view window


# ── Geometry helpers ────────────────────────────────────────────────────────
def _vec(h: float, p: float) -> tuple[float, float, float]:
    hr, pr = math.radians(h), math.radians(p)
    return (math.cos(pr) * math.sin(hr), math.cos(pr) * math.cos(hr), math.sin(pr))


def _norm(v) -> float:
    return math.sqrt(sum(c * c for c in v))


def _angle(a, b) -> float:
    d = sum(x * y for x, y in zip(a, b, strict=True)) / ((_norm(a) * _norm(b)) or 1.0)
    return math.degrees(math.acos(max(-1.0, min(1.0, d))))


def _heading_pitch(v) -> tuple[float, float]:
    n = _norm(v) or 1.0
    return (
        math.degrees(math.atan2(v[0], v[1])) % 360,
        math.degrees(math.asin(max(-1.0, min(1.0, v[2] / n)))),
    )


def _arc(headings: list[float]) -> tuple[float, float]:
    """(span, center) of the smallest arc of the compass that holds every heading."""
    hs = sorted(h % 360 for h in headings)
    n = len(hs)
    if n == 1:
        return 0.0, hs[0]
    gaps = [hs[i + 1] - hs[i] for i in range(n - 1)] + [hs[0] + 360 - hs[-1]]
    k = max(range(n), key=gaps.__getitem__)
    start = hs[(k + 1) % n]
    span = 360 - gaps[k]
    return span, (start + span / 2) % 360


def _fov(zoom: float) -> float:
    return FOV_AT_ZOOM_0 / 2**zoom


def view_of(pano, heading: float, pitch: float, zoom: float) -> dict | None:
    """The view the UI turns into a Street View photo, or None when the spot is unknown."""
    if not pano:
        return None
    fov = max(VIEW_FOV_MIN, min(VIEW_FOV_MAX, _fov(zoom)))
    return {
        "lat": round(pano[0], 6),
        "lng": round(pano[1], 6),
        "heading": round(heading % 360, 1),
        "pitch": round(max(-90.0, min(90.0, pitch)), 1),
        "fov": round(fov, 1),
    }


def _cover_view(group) -> tuple[float, float, float]:
    """(heading, pitch, zoom) of the zoomed-OUT view that contains every frame seen in the group.

    Each frame is its own heading/pitch at its own zoom. The result is the widest of them all, no
    averaging: if the frames spread out, the zoom drops accordingly (it can drop below ZOOM_IN_MIN,
    and then the caller generates nothing).
    """
    heads: list[float] = []
    lows: list[float] = []
    highs: list[float] = []
    for _, _, h, p, z in group:
        half = _fov(z) / 2
        heads += [h - half, h + half]
        lows.append(max(-90.0, p - half / VIEW_ASPECT))
        highs.append(min(90.0, p + half / VIEW_ASPECT))
    hspan, hcenter = _arc(heads)
    need = max(hspan, (max(highs) - min(lows)) * VIEW_ASPECT)
    zoom = math.log2(FOV_AT_ZOOM_0 / need) if need > 0 else math.inf
    return hcenter, (max(highs) + min(lows)) / 2, zoom


def _move_range(samples) -> tuple[int, int]:
    """Indexes of the first/last sample of the part where the camera was actually turning."""
    n = len(samples)
    first, last = _vec(samples[0][1], samples[0][2]), _vec(samples[-1][1], samples[-1][2])
    a = 0
    while (
        a + 1 < n and _angle(_vec(samples[a + 1][1], samples[a + 1][2]), first) <= STARE_DRIFT_DEG
    ):
        a += 1
    b = n - 1
    while b - 1 > a and _angle(_vec(samples[b - 1][1], samples[b - 1][2]), last) <= STARE_DRIFT_DEG:
        b -= 1
    return a, b


# ── One segment ─────────────────────────────────────────────────────────────
def _segment_looks(seg: dict, t_end: float) -> list[dict]:
    merged: list = []
    for s in seg["samples"]:  # (t, heading, pitch, zoom)
        if merged and merged[-1][0] == s[0]:
            merged[-1] = s
        else:
            merged.append(s)
    # Intervals: the state that held from one sample to the next (the last one until the segment ends).
    ivs = []
    for i, (t, h, p, z) in enumerate(merged):
        t1 = merged[i + 1][0] if i + 1 < len(merged) else t_end
        if t1 > t:
            ivs.append((t, t1, h, p, z))
    if not ivs:
        return []

    pano = seg["pano"]
    focus = {"points": [[round(pano[0], 6), round(pano[1], 6)]]} if pano else None

    def view(h, p, z) -> dict:
        return {"view": v} if (v := view_of(pano, h, p, z)) else {}

    def make(t, key, dur, **data) -> dict:
        ph = {"t": t, "kind": "look", "key": key, "dur_ms": dur, **data}
        if focus:
            ph["focus"] = focus
        return ph

    zooms: list[dict] = []
    stares: list[dict] = []
    for zoomed, grp in itertools.groupby(ivs, key=lambda iv: iv[4] >= ZOOM_IN_MIN):
        g = list(grp)
        if g[-1][1] - g[0][0] < LOOK_MIN_MS:
            continue
        if zoomed:
            h, p, z = _cover_view(g)
            if z + 1e-6 >= ZOOM_IN_MIN:  # otherwise the spread is too wide to be one zoomed look
                zooms.append(make(g[0][0], "look_zoom", g[-1][1] - g[0][0], **view(h, p, z)))
            continue
        # Not zoomed: split into runs where the camera stays (roughly) in place.
        runs: list[dict] = []
        cur = None
        for iv in g:
            v, w = _vec(iv[2], iv[3]), iv[1] - iv[0]
            if cur and _angle(cur["sum"], v) <= STARE_DRIFT_DEG:
                cur["ivs"].append(iv)
                cur["sum"] = tuple(a + b * w for a, b in zip(cur["sum"], v, strict=True))
            else:
                cur = {"ivs": [iv], "sum": tuple(c * w for c in v)}
                runs.append(cur)
        for r in runs:
            ivr = r["ivs"]
            dur = ivr[-1][1] - ivr[0][0]
            if dur < LOOK_MIN_MS:
                continue
            h, p = _heading_pitch(r["sum"])
            total = sum(x[1] - x[0] for x in ivr) or 1
            z = sum(x[4] * (x[1] - x[0]) for x in ivr) / total
            stares.append(make(ivr[0][0], "look_stare", dur, **view(h, p, z)))

    sweeps: list[dict] = []
    span, _ = _arc([s[1] for s in merged])
    if span >= SWEEP_MIN_DEG:
        if zooms:
            zooms[0]["around"] = True  # "...while looking around at your surroundings"
        else:
            a, b = _move_range(merged)
            if b > a:
                dur = merged[b][0] - merged[a][0]
                ratio = dur / SWEEP_EXPECTED_MS
                if ratio < SWEEP_QUICK_RATIO:
                    tier = "quick"
                elif ratio >= SWEEP_REALLY_PATIENT_RATIO:
                    tier = "really_patient"
                elif ratio >= SWEEP_PATIENT_RATIO:
                    tier = "patient"
                else:
                    tier = "normal"
                sweeps.append(make(merged[a][0], "look_sweep", dur, tier=tier, span=round(span)))
    return [*zooms, *stares, *sweeps]


# ── Whole round ─────────────────────────────────────────────────────────────
def analyze_looks(events: list[dict]) -> dict:
    """{"phases": [...look phases], "opens": [{"t", "url"}]} for one compressed replay.

    "opens" = map openings that came right after a zoomed-in view (t = the moment the map opened).
    """
    phases: list[dict] = []
    opens: list[dict] = []
    state = {"h": None, "p": None, "z": 0.0}
    seg = None
    pano = None
    map_open = False
    last_view_t = None
    last_type = None
    done = False

    def start(t) -> None:
        nonlocal seg
        seg = {"t0": t, "pano": pano, "samples": []}
        if state["h"] is not None:
            seg["samples"].append((t, state["h"], state["p"], state["z"]))

    def end(t) -> None:
        nonlocal seg
        if seg is not None:
            phases.extend(_segment_looks(seg, t))
            seg = None

    for ev in events:
        t, typ, p = ev["time"], ev["type"], ev.get("payload") or {}
        if typ == "PanoPosition" and p.get("lat") is not None:
            end(t)
            pano = (p["lat"], p["lng"])
            if not map_open:
                start(t)
        elif typ == "PanoPov":
            h, pt = p.get("heading"), p.get("pitch")
            if isinstance(h, int | float) and isinstance(pt, int | float):
                state["h"], state["p"] = h % 360, pt
                last_view_t = t
                if seg is not None:
                    seg["samples"].append((t, state["h"], state["p"], state["z"]))
        elif typ == "PanoZoom":
            z = p.get("zoom")
            if isinstance(z, int | float):
                state["z"] = float(z)
                last_view_t = t
                if seg is not None and state["h"] is not None:
                    seg["samples"].append((t, state["h"], state["p"], state["z"]))
        elif typ == "Aligning":
            end(t)
            state["h"] = state["p"] = None
            if not map_open:
                start(t)
        elif typ == "MapDisplay":
            if p.get("isActive") and not map_open:
                if (
                    pano
                    and state["h"] is not None
                    and state["z"] + 1e-6 >= ZOOM_IN_MIN
                    and last_type in {"PanoPov", "PanoZoom"}  # looking was the very last thing done
                    and t - last_view_t <= OPEN_AFTER_LOOK_MS
                ):
                    opens.append(
                        {"t": t, "view": view_of(pano, state["h"], state["p"], state["z"])}
                    )
                end(t)
                map_open = True
            elif not p.get("isActive") and map_open:
                map_open = False
                start(t)
        elif typ == "GuessWithLatLng":
            end(t)
            done = True
            break
        last_type = typ
    if not done and events:
        end(events[-1]["time"])
    return {"phases": phases, "opens": opens}


def add_looks(phases: list[dict], events: list[dict]) -> list[dict]:
    """Merge look events into the phases from analyze_round. Call before grade_phases.

    * "You stopped to look for clues" is shortened by the time the look events already explain
      (and dropped if what is left is below the pause threshold).
    * A map opening right after a zoomed-in view becomes a "look_open" event. When the first zoom
      of that map session is told, narrate() folds it into that sentence as a prefix instead
      (phase["pre_look"] = the view, phase["pre_look_t"] = when the map opened).
    """
    if not events:
        return phases
    from src.analysis.replay_behavior import (  # noqa: PLC0415
        _PRIORITY,
        OPEN_ZOOM_MS,
        PAUSE_MS,
    )

    res = analyze_looks(events)
    looks = res["phases"]
    spans = [(p["t"], p["t"] + p["dur_ms"]) for p in looks]
    out = []
    for p in phases:
        if p["key"] == "pause":
            lo, hi = p["t"], p["t"] + p["dur_ms"]
            covered = sum(max(0, min(hi, b) - max(lo, a)) for a, b in spans)
            rest = p["dur_ms"] - min(covered, p["dur_ms"])
            if rest < PAUSE_MS:
                continue
            p["dur_ms"] = rest
        if p["key"] == "zoom_in":
            for o in res["opens"]:
                if 0 <= p["t"] - o["t"] <= OPEN_ZOOM_MS:
                    p["pre_look"] = o["view"]
                    p["pre_look_t"] = o["t"]
        out.append(p)
    out.extend(looks)
    for o in res["opens"]:
        v = o["view"]
        out.append(
            {
                "t": o["t"] - 1,  # just before the map events of that moment
                "kind": "look",
                "key": "look_open",
                "dur_ms": 0,
                "open_t": o["t"],
                "view": v,
                "focus": {"points": [[v["lat"], v["lng"]]]},
            },
        )
    out.sort(key=lambda p: (p["t"], _PRIORITY.get(p["kind"], 5)))
    return out
