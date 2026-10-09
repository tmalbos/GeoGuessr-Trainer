"""replay_stages.py — Split a round into four stages and measure the time and steps spent in each.

Stages are decided by where the player's pin is, then by whether the map is open.
First match wins, checked at every moment of the round:
    verify  the pin is already a 5K (within ~150 m) and the player is still doing things
    pin     the pin is in the real town/city area (within PINPOINT_KM) but not a 5K yet
    map     the map is open (and neither of the above)
    clues   anything else (street view, no good pin yet)
A pin only counts while it is the latest one, so moving a 5K pin away drops you back a stage.
"""

import itertools

from src.analysis.replay_behavior import _parse, km_between  # noqa: PLC2701
from src.analysis.scoring import dist_to_score

STAGES = ("clues", "map", "pin", "verify")
PINPOINT_KM = 10.0  # a pin this close to the real spot is "in the town/city area"


def is_5k(km: float) -> bool:
    return dist_to_score(km) >= 5000


def _pin_state(km: float) -> str | None:
    if is_5k(km):
        return "verify"
    if km <= PINPOINT_KM:
        return "pin"
    return None


def stage_breakdown(events: list[dict], real: dict) -> dict | None:
    """events: compressed replay; real: {lat, lng, ...}. Returns None when there is no replay.

    {duration_ms, segments: [{stage, t0, t1}], time_ms: {stage: ms}, steps: {stage: n}}
    """
    if not events:
        return None
    s = _parse(events)
    start = events[0]["time"]
    end = s["guess"][0] if s["guess"] else events[-1]["time"]
    sessions = s["sessions"]
    pin_states = [
        (t, _pin_state(km_between(lat, lng, real["lat"], real["lng"])))
        for t, lat, lng in s["pins"]
        if t <= end
    ]

    def stage_at(t: float) -> str:
        state = None
        for pin_t, st in pin_states:  # chronological: the last pin placed so far wins
            if pin_t > t:
                break
            state = st
        if state:
            return state
        in_map = any(x["start"] <= t < x["end"] for x in sessions)
        return "map" if in_map else "clues"

    cuts = {start, end}
    for x in sessions:
        cuts.update((x["start"], x["end"]))
    cuts.update(t for t, _ in pin_states)
    ordered = sorted(c for c in cuts if start <= c <= end)

    segments: list[dict] = []
    time_ms = dict.fromkeys(STAGES, 0)
    for a, b in itertools.pairwise(ordered):
        stage = stage_at(a)
        time_ms[stage] += b - a
        if segments and segments[-1]["stage"] == stage:
            segments[-1]["t1"] = b
        else:
            segments.append({"stage": stage, "t0": a, "t1": b})

    # The first pano is where the round starts, not a step.
    steps = dict.fromkeys(STAGES, 0)
    for t, _, _ in s["panos"][1:]:
        if t <= end:
            steps[stage_at(t)] += 1

    return {"duration_ms": end - start, "segments": segments, "time_ms": time_ms, "steps": steps}


def summarize_stages(stages: list[dict | None]) -> dict:
    """Whole-game totals. Rounds without a replay are left out."""
    steps = dict.fromkeys(STAGES, 0)
    time_ms = dict.fromkeys(STAGES, 0)
    have = [st for st in stages if st]
    for st in have:
        for k in STAGES:
            steps[k] += st["steps"][k]
            time_ms[k] += st["time_ms"][k]
    return {
        "rounds_total": len(stages),
        "rounds_with_replay": len(have),
        "steps": steps,
        "time_ms": time_ms,
    }
