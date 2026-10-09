"""replay_grading.py — Chess-style grade for each event of a round.

Sets phase["grade"] on the phases from analyze_round (empty string = not graded).

Grades, best to worst: brilliant, great, best, excellent, good, inaccuracy, miss, mistake, blunder.

Two kinds of events are graded:
  * Where the player is looking or pinning (zoom, scan and pin events). A map view is judged by
    EVERYTHING it shows, not by its center (the player sees the periphery too):
      - errors (blunder / mistake / inaccuracy) use the CLOSEST point of the view: the view only
        counts as wrong if even its best part scores under the threshold;
      - good qualifiers (good / excellent) use the FURTHEST point of the view: the whole view
        has to be inside the threshold.
    A view that is neither (e.g. the right country, zoomed out) is left ungraded.
    A pin is a single point, so it is both at once. Being in the wrong country doubles the distance.
  * Street view moves: by efficiency (backtracking, revisiting a pano, or a clean single-direction path).
"""

from collections import Counter

from src.analysis.replay_behavior import _parse, km_between  # noqa: PLC2701
from src.analysis.replay_stages import PINPOINT_KM, is_5k
from src.analysis.scoring import SCORE_CUTOFFS, dist_to_score

# ── Tunables ────────────────────────────────────────────────────────────────
WRONG_COUNTRY_FACTOR = (
    2  # distances are multiplied by this when the position is in the wrong country
)
MISS_MIN_ZOOM = 10  # zoom needed to say the player was looking AT the real spot
BRILLIANT_MS = 45_000  # a 5K pinned faster than this is brilliant
GREAT_MS = 90_000  # ...faster than this is great
GREAT_MOVES = 10  # ...or with fewer moves than this is great
EFFICIENT_SCORE = 4500  # a single-direction round scoring above this has "good" moves
REVISITS = 3  # standing on the same pano this many times is an inaccuracy

GRADE_ORDER = (
    "brilliant",
    "great",
    "best",
    "excellent",
    "good",
    "inaccuracy",
    "miss",
    "mistake",
    "blunder",
)


def _error_grade(score: int) -> str:
    """Grade for a position that is wrong (score under the 'good' cut-off)."""
    blunder, mistake, _, _ = SCORE_CUTOFFS
    if score < blunder:
        return "blunder"
    if score < mistake:
        return "mistake"
    return "inaccuracy"


def _grade_moves(phases: list[dict], panos: list, final_score: int) -> None:
    keys = [(round(la, 5), round(ln, 5)) for _, la, ln in panos]
    seen: Counter = Counter()
    revisit_times = []
    for (t, _, _), key in zip(panos, keys, strict=True):
        seen[key] += 1
        if seen[key] >= REVISITS:
            revisit_times.append(t)
    linear = len(set(keys)) == len(keys)  # never stood on the same pano twice
    last_pano_t = panos[-1][0] if panos else None

    for p in phases:
        if p["kind"] != "move":
            continue
        span_end = p["t"] + p.get("dur_ms", 0)
        if any(p["t"] <= t <= span_end for t in revisit_times):
            p["grade"] = "inaccuracy"
        elif p["key"] == "return_start":
            # Went back to spawn and then set off again.
            if last_pano_t is not None and last_pano_t > p["t"]:
                p["grade"] = "inaccuracy"
        elif linear and final_score > EFFICIENT_SCORE:
            p["grade"] = "good"


def _view_extent(boxes: list, real: dict):
    """Closest and furthest point of the given map windows (west, south, east, north) to the real spot.

    Returns ((km, (lat, lng)), (km, (lat, lng))).
    """
    near = (float("inf"), None)
    far = (-1.0, None)
    for west, south, east, north in boxes:
        lat = min(max(real["lat"], south), north)
        lng = min(max(real["lng"], west), east)
        d = km_between(lat, lng, real["lat"], real["lng"])
        if d < near[0]:
            near = (d, (lat, lng))
        for c_lat in (south, north):
            for c_lng in (west, east):
                d = km_between(c_lat, c_lng, real["lat"], real["lng"])
                if d > far[0]:
                    far = (d, (c_lat, c_lng))
    return near, far


def _grade_map(phases: list[dict], panos: list, t0: float, real: dict, locate) -> None:
    def score_at(km: float, point) -> int:
        a = locate(*point)
        code = a.get("country_code") if a else None
        wrong = bool(code and real["country_code"] and code != real["country_code"])
        return dist_to_score(km * (WRONG_COUNTRY_FACTOR if wrong else 1))

    _, _, good_cut, excellent_cut = SCORE_CUTOFFS
    prev = None  # the last looking/pinning event
    five_k_done = False
    for p in phases:
        kind = p["kind"]
        is_pin = kind == "pin"
        if is_pin:
            point = (p["lat"], p["lng"])
            near = far = (p["dist_km"], point)
        elif kind in {"zoom", "scan"} and p.get("view_boxes"):
            near, far = _view_extent(p["view_boxes"], real)
        else:
            continue

        near_km = near[0]
        s_near = score_at(*near)
        s_far = s_near if is_pin else score_at(*far)
        zoom = p.get("zoom") or 0
        on_target = (
            near_km <= PINPOINT_KM if is_pin else (near_km <= 0.001 and zoom >= MISS_MIN_ZOOM)
        )

        miss = False
        if prev and prev["on_target"]:
            if prev["is_pin"]:
                miss = is_pin and s_near < prev["score"]  # had a close pin, moved it further away
            else:
                miss = near_km > PINPOINT_KM  # was looking at the real spot, moved on elsewhere

        grade = ""
        if miss:
            grade = "miss"
        elif s_near < good_cut:
            grade = _error_grade(s_near)  # even the best part of what they see is wrong
        elif is_pin and is_5k(near_km):
            grade = "best"
            if not five_k_done:
                five_k_done = True
                elapsed = p["t"] - t0
                moves = sum(1 for t, _, _ in panos[1:] if t <= p["t"])
                if moves == 0 or elapsed < BRILLIANT_MS:
                    grade = "brilliant"
                elif elapsed < GREAT_MS or moves < GREAT_MOVES:
                    grade = "great"
        elif s_far >= excellent_cut:
            grade = "excellent"  # everything they see is within the excellent range
        elif s_far >= good_cut:
            grade = "good"

        p["grade"] = grade
        prev = {"on_target": on_target, "is_pin": is_pin, "score": s_near}


def grade_phases(phases: list[dict], events: list[dict], real: dict, locate) -> None:
    """Add a "grade" to every phase. `locate(lat, lng)` -> admin dict or None, like analyze_round."""
    for p in phases:
        p["grade"] = ""
    if not events:
        return
    s = _parse(events)
    final_score = next((p["score"] for p in phases if p["key"] == "guess"), 0)
    _grade_moves(phases, s["panos"], final_score)
    _grade_map(phases, s["panos"], events[0]["time"], real, locate)


def summarize_grades(grades: list[str]) -> list[dict]:
    """[{grade, count}] in GRADE_ORDER, only the grades that happened."""
    counts = Counter(grades)
    return [{"grade": g, "count": counts[g]} for g in GRADE_ORDER if counts[g]]
