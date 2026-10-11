"""replay_grading.py — Chess-style grade for each event of a round.

Sets phase["grade"] on the phases from analyze_round (empty string = not graded).

Grades, best to worst: brilliant, great, best, excellent, good, inaccuracy, miss, mistake, blunder.

Three families of events are graded:

  1. EFFICIENCY (how long something took against what it should take). The ratio is
     actual time / expected time, and the same ladder is used everywhere:
         < 1.25 best · < 1.5 excellent · < 1.75 good · < 2 inaccuracy · < 3 mistake · else blunder
     Applies to step runs (expected = STEP_EXPECTED_S per step) and slow zooms
     (expected = ZOOM_STEP_S per zoom level). Staring at one zoom level is the same ladder turned
     around (more time = worse, and only from 1.5x the expected 10 s). Also graded here:
     pointless open/close of the map, erratic revisits, going back to spawn for the 3rd time.

  2. WHERE THE PLAYER LOOKS OR PINS (map views and pins). A map view is judged by EVERYTHING it
     shows, not by its center:
       - errors (blunder / mistake / inaccuracy) use the CLOSEST point of the view: the view only
         counts as wrong if even its best part scores under the threshold;
       - zooming in until the view contains the real spot is "best";
       - otherwise good qualifiers (good / excellent) use the FURTHEST point of the view.
     A pin is a single point, so it is both at once. Being in the wrong country doubles the
     distance. "Miss" = the player had the real spot in view (or a close pin) and moved away.
     To avoid noise, a good/excellent/best mark is not repeated while the player keeps being
     "as good as before": it only shows again if the grade changes or the view/pin got more precise.

  3. THE GUESS ITSELF, graded by its score.
"""

import math
from collections import Counter

from src.analysis.replay_behavior import REVISITS, _parse, km_between  # noqa: PLC2701
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
HIT_KM = 0.001  # a map window whose closest point is this near contains the real spot

# actual/expected time ratio -> grade (upper bounds, exclusive)
RATIO_LADDER = (
    (1.25, "best"),
    (1.5, "excellent"),
    (1.75, "good"),
    (2.0, "inaccuracy"),
    (3.0, "mistake"),
)
POSITIVE = {"best", "excellent", "good"}
# How precise what the player is looking at is (used to decide if a repeated mark is new info)
RANK = {"world": 0, "wide": 1, "country": 2, "region": 3, "city": 4, "point": 5}
PIN_RANK = 6

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


def ratio_grade(ratio: float) -> str:
    for cut, grade in RATIO_LADDER:
        if ratio < cut:
            return grade
    return "blunder"


def _stare_grade(ratio: float) -> str:
    """Staring is the ladder turned around: it only gets noticed from 1.5x the expected time."""
    if ratio < 1.5:
        return ""
    return ratio_grade(ratio)


def _error_grade(score: int) -> str:
    """Grade for a position that is wrong (score under the 'good' cut-off)."""
    blunder, mistake, _, _ = SCORE_CUTOFFS
    if score < blunder:
        return "blunder"
    if score < mistake:
        return "mistake"
    return "inaccuracy"


def _grade_efficiency(phases: list[dict]) -> None:
    for p in phases:
        key = p["key"]
        if key in {"run", "speed_move", "zoom_start"}:
            p["grade"] = ratio_grade(p["ratio"])
        elif key == "stare":
            p["grade"] = _stare_grade(p["ratio"])
        elif key in {"quick_map", "erratic", "move_revisit"} or (
            key == "return_start" and p["visit"] >= REVISITS
        ):
            p["grade"] = "inaccuracy"


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


def _near_point(box_, center, pin) -> bool:
    """Is the view centered around the given pin (within half of the view's half-width)?"""
    if not pin or not center:
        return False
    west, _south, east, _north = box_
    half_w_km = (east - west) / 2 * 111.32 * max(0.05, math.cos(math.radians(center[0])))
    return km_between(center[0], center[1], pin[0], pin[1]) <= 0.5 * half_w_km


def _grade_map(phases: list[dict], panos: list, t0: float, real: dict, locate) -> None:
    def score_at(km: float, point) -> int:
        a = locate(*point)
        code = a.get("country_code") if a else None
        wrong = bool(code and real["country_code"] and code != real["country_code"])
        return dist_to_score(km * (WRONG_COUNTRY_FACTOR if wrong else 1))

    _, _, good_cut, excellent_cut = SCORE_CUTOFFS
    prev_view_hit = False  # the last map view showed the real spot (zoomed in enough)
    prev_pin = None  # {"km", "score"} of the last pin
    last_pin_pt = None
    last_mark = None  # (grade, rank) of the last good/excellent/best shown in the view/pin chain
    five_k_done = False

    def remember(grade: str, rank: int) -> str:
        """Hide a positive mark that adds nothing over the previous one."""
        nonlocal last_mark
        if grade in POSITIVE:
            if last_mark and last_mark[0] == grade and rank <= last_mark[1]:
                return ""
            last_mark = (grade, rank)
            return grade
        if grade:
            last_mark = (grade, 0)
        return grade

    for p in phases:
        kind = p["kind"]
        if kind == "pin":
            point = (p["lat"], p["lng"])
            km = p["dist_km"]
            s = score_at(km, point)
            miss = (prev_view_hit and km > PINPOINT_KM) or (
                prev_pin is not None and prev_pin["km"] <= PINPOINT_KM and s < prev_pin["score"]
            )
            grade = ""
            if miss:
                grade = "miss"
            elif s < good_cut:
                grade = _error_grade(s)
            elif is_5k(km):
                grade = "best"
                if not five_k_done:
                    five_k_done = True
                    elapsed = p["t"] - t0
                    moves = sum(1 for t, _, _ in panos[1:] if t <= p["t"])
                    if moves == 0 or elapsed < BRILLIANT_MS:
                        grade = "brilliant"
                    elif elapsed < GREAT_MS or moves < GREAT_MOVES:
                        grade = "great"
            elif s >= excellent_cut:
                grade = "excellent"
            else:
                grade = "good"
            if grade in {"brilliant", "great"}:
                last_mark = (grade, PIN_RANK)
            else:
                grade = remember(grade, PIN_RANK)
            p["grade"] = grade
            prev_pin = {"km": km, "score": s}
            last_pin_pt = point
        elif kind in {"zoom", "scan"} and p.get("view_boxes"):
            near, far = _view_extent(p["view_boxes"], real)
            near_km = near[0]
            hit = near_km <= HIT_KM
            s_near = score_at(*near)
            s_far = s_near if hit else score_at(*far)
            zoom = p.get("zoom") or 0
            level = p.get("level")
            progress = p["key"] != "zoom_out"  # zooming out is never praised

            miss = (prev_view_hit and not hit) or (
                prev_pin is not None and prev_pin["km"] <= PINPOINT_KM and near_km > PINPOINT_KM
            )
            grade = ""
            if miss:
                grade = "miss"
            elif s_near < good_cut:
                grade = _error_grade(s_near)  # even the best part of what they see is wrong
            elif progress:
                if hit and kind == "zoom" and level not in {"world", "wide", "", None}:
                    grade = "best"  # zoomed in with the real spot in view
                elif s_far >= excellent_cut:
                    grade = "excellent"  # everything they see is within the excellent range
                elif s_far >= good_cut:
                    grade = "good"
            p["hit"] = hit
            if miss and _near_point(p["view_boxes"][0], p.get("center"), last_pin_pt):
                p["near_pin"] = True
            p["grade"] = remember(grade, RANK.get(level, 1))
            prev_view_hit = hit and zoom >= MISS_MIN_ZOOM


def _grade_guess(phases: list[dict]) -> None:
    _, _, good_cut, excellent_cut = SCORE_CUTOFFS
    for p in phases:
        if p["key"] != "guess":
            continue
        score = p["score"]
        if score >= 5000:
            p["grade"] = "best"
        elif score < good_cut:
            p["grade"] = _error_grade(score)
        elif score >= excellent_cut:
            p["grade"] = "excellent"
        else:
            p["grade"] = "good"


def grade_phases(phases: list[dict], events: list[dict], real: dict, locate) -> None:
    """Add a "grade" to every phase. `locate(lat, lng)` -> admin dict or None, like analyze_round."""
    for p in phases:
        p["grade"] = ""
    if not events:
        return
    s = _parse(events)
    _grade_efficiency(phases)
    _grade_map(phases, s["panos"], events[0]["time"], real, locate)
    _grade_guess(phases)


def summarize_grades(grades: list[str]) -> list[dict]:
    """[{grade, count}] in GRADE_ORDER, only the grades that happened."""
    counts = Counter(grades)
    return [{"grade": g, "count": counts[g]} for g in GRADE_ORDER if counts[g]]
