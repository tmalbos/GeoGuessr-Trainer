"""replay_savings.py — Time and steps a round could have saved.

Three sources, attributed in this order so nothing is counted twice (time is the UNION of the
intervals, steps are a SET of step indexes):

  verify  Everything in the "verify" stage except its first VERIFY_ALLOWED_MS (a human is never
          instant), plus every step taken in it. The ideal is almost no verification.
  repeat  A step onto a position the player had already been on (spawn included) is a wasted step.
          Its time is the time that step took (since the previous step, capped at
          STEP_TIME_CAP_MS). Time counts even while the map is open; a step taken while the map
          is open is NOT counted as a wasted step.
  map     Map time spent looking at the wrong place while standing still in street view (no step
          taken during that map session): scanning another country, a zoom that lands far from the
          real spot (grade inaccuracy / miss / mistake / blunder), a hesitation towards another
          country. The bad time runs from that event to the next map event, or until the map closes.
  slow    Moving / zooming slower than an efficient player (the old calculation).
"""

import operator

from src.analysis.replay_behavior import (
    MOVE_RUN_GAP_MS,
    START_RETURN_M,
    _pano_key,  # noqa: PLC2701
    _parse,  # noqa: PLC2701
    km_between,
)
from src.analysis.replay_narrative import SAVING_GRADES

VERIFY_ALLOWED_MS = 1000
STEP_TIME_CAP_MS = MOVE_RUN_GAP_MS
PARTS = ("verify", "repeat", "map", "slow")
BAD_GRADES = {"inaccuracy", "miss", "mistake", "blunder"}


def _map_event(p: dict) -> bool:
    """Events that say where the map is looking (zoom_speed is about speed, not place)."""
    key = p["key"]
    return key in {"zoom_in", "zoom_out", "hesitate"} or key.startswith(("scan_", "pin_"))


def _bad_map_event(p: dict) -> bool:
    if p["key"].startswith("pin_"):
        return False
    return p["key"] == "scan_country_wrong" or p.get("grade") in BAD_GRADES


def _part() -> dict:
    return {"time_ms": 0, "steps": 0}


def empty() -> dict:
    return {"time_ms": 0, "steps": 0, "parts": {k: _part() for k in PARTS}}


def _union_length(intervals: list[tuple[float, float]]) -> float:
    total, end = 0.0, None
    for a, b in sorted(i for i in intervals if i[1] > i[0]):
        if end is None or a > end:
            total += b - a
            end = b
        elif b > end:
            total += b - end
            end = b
    return total


def round_savings(events: list[dict], stages: dict | None, phases: list[dict]) -> dict:
    """events: compressed replay. stages: stage_breakdown() result. phases: graded phases."""
    if not events or not stages:
        return empty()
    s = _parse(events)
    panos, sessions = s["panos"], s["sessions"]
    end = events[0]["time"] + stages["duration_ms"]

    # verify: time beyond the allowance + every step inside the stage
    verify_segs = [(g["t0"], g["t1"]) for g in stages["segments"] if g["stage"] == "verify"]
    verify_iv: list[tuple[float, float]] = []
    allow = VERIFY_ALLOWED_MS
    for a, b in verify_segs:
        cut = min(allow, b - a)
        allow -= cut
        verify_iv.append((a + cut, b))
    verify_steps = {
        i
        for i, (t, _, _) in enumerate(panos)
        if i and t <= end and any(a <= t < b for a, b in verify_segs)
    }

    # repeat: steps onto positions already visited
    repeat_iv: list[tuple[float, float]] = []
    repeat_steps: set[int] = set()
    if panos:
        first = panos[0]
        seen = {_pano_key(first)}
        prev = first[0]
        for i, p in enumerate(panos[1:], start=1):
            t, lat, lng = p
            if t > end:
                break
            back_at_spawn = km_between(lat, lng, first[1], first[2]) * 1000 <= START_RETURN_M
            if _pano_key(p) in seen or back_at_spawn:
                repeat_iv.append((t - min(t - prev, STEP_TIME_CAP_MS), t))
                if not any(x["start"] <= t < x["end"] for x in sessions):
                    repeat_steps.add(i)
            seen.add(_pano_key(p))
            prev = t

    # map: time looking at the wrong place while standing still
    map_iv: list[tuple[float, float]] = []
    for sess in sessions:
        a, b = sess["start"], sess["end"]
        if any(a <= t < b for t, _, _ in panos):
            continue  # the player moved during this map session
        evs = sorted(
            (p for p in phases if a <= p["t"] <= b and _map_event(p)), key=operator.itemgetter("t")
        )
        for i, p in enumerate(evs):
            if _bad_map_event(p):
                stop = next((q["t"] for q in evs[i + 1 :] if q["t"] > p["t"]), b)
                map_iv.append((p["t"], min(stop, b)))

    # slow: time over what an efficient player needs
    slow_iv: list[tuple[float, float]] = []
    for p in phases:
        if p.get("speed_grade") not in SAVING_GRADES:
            continue
        if p["key"] in {"run", "speed_move"}:
            start = p["t"]
        elif p["key"] == "zoom_speed":
            start = p["t"] - p["dur_ms"]
        else:
            continue
        slow_iv.append((start + p["expected_ms"], start + p["dur_ms"]))

    out = empty()
    cum_iv: list[tuple[float, float]] = []
    cum_steps: set[int] = set()
    for name, iv, steps in (
        ("verify", verify_iv, verify_steps),
        ("repeat", repeat_iv, repeat_steps),
        ("map", map_iv, set()),
        ("slow", slow_iv, set()),
    ):
        before_t, before_s = _union_length(cum_iv), len(cum_steps)
        cum_iv += iv
        cum_steps |= steps
        out["parts"][name] = {
            "time_ms": round(_union_length(cum_iv) - before_t),
            "steps": len(cum_steps) - before_s,
        }
    out["time_ms"] = round(_union_length(cum_iv))
    out["steps"] = len(cum_steps)
    return out


def merge_parts(parts_list: list[dict]) -> dict:
    total = {k: _part() for k in PARTS}
    for parts in parts_list:
        for k in PARTS:
            total[k]["time_ms"] += parts[k]["time_ms"]
            total[k]["steps"] += parts[k]["steps"]
    return total
