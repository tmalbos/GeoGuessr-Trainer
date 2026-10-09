"""replay_narrative.py — Layer 2: turn behavior phases into report sentences.

One template per phase key for now. To add variety later, make a value a list of templates
and pick one (seed the choice by game + round so a report doesn't change on reload).
"""

TEXT = {
    "start": "The round started in {place}.",
    "move": "You advanced to {place}.",
    "speed_move": "You speed-moved {steps} steps and ended up in {place}.",
    "return_start": "You went back to where the round started.",
    "pause": "You stopped to look for clues for {dur}.",
    "map_open": "You opened the map for {dur}.",
    "zoom_in": "You zoomed into {place}.",
    "zoom_out": "You zoomed out to {place}.",
    "scan_country": "You seemed to know you were in {place}, but scanned across {n_regions} of its regions without a clear one in mind ({dur}).",
    "scan_country_wrong": "You scanned across {place} ({n_regions} regions, {dur}), but the round was actually in {real_place}.",
    "scan_multi_country": "You searched across several countries: {place} ({dur}).",
    "scan_regions_hit": "You started searching inside {place}, which included the right region ({dur}).",
    "scan_regions_miss": "You started searching inside {place}, but the real spot was in {real_place} ({dur}).",
    "scan_area": "You scanned the map around {place} ({dur}).",
    "align": "You aligned the view with the road.",
    "align_region": "You had narrowed it down to {place} and started aligning the road.",
    "align_city": "You identified this was {place} and started trying to align the road.",
    "pin_exact": "You put a pin on the exact spot, in {place}.",
    "pin_right_region": "You placed a pin in {place}, the right region but {km} km off.",
    "pin_wrong_region": "You placed a pin in {place}, the wrong region, {km} km off.",
    "pin_wrong_country": "You placed a pin in {place}, the wrong country, {km} km off.",
    "pin_off": "You placed a pin near {place}, {km} km from the real spot.",
    "verify_exact": "You had pinned the exact spot, but spent {dur} verifying it before confirming.",
    "verify_other": "After pinning, you went back to street view for {dur} before confirming.",
    "guess": "You confirmed your guess: {score} points.",
    "no_guess": "The round ended without a guess.",
}

TONE = {
    "pin_exact": "good",
    "pin_right_region": "good",
    "pin_wrong_region": "bad",
    "pin_wrong_country": "bad",
    "scan_country_wrong": "bad",
    "scan_regions_miss": "bad",
    "scan_country": "bad",
    "no_guess": "bad",
}


def _dur(ms: float) -> str:
    s = round(ms / 1000)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def _km(d: float) -> str:
    return f"{d:.1f}" if d < 10 else f"{d:,.0f}"


def narrate(phases: list[dict]) -> list[dict]:
    """Phases -> [{t_ms, kind, tone, grade, text, count, lat?, lng?}]. Identical neighbours are merged (count)."""
    out: list[dict] = []
    for p in phases:
        ctx = {**p, "dur": _dur(p.get("dur_ms", 0)), "km": _km(p.get("dist_km", 0))}
        text = TEXT[p["key"]].format(**ctx)
        grade = p.get("grade") or ""
        if (
            out
            and out[-1]["kind"] == p["kind"]
            and out[-1]["text"] == text
            and out[-1]["grade"] == grade
        ):
            out[-1]["count"] += 1
            continue
        item = {
            "t_ms": p["t"],
            "kind": p["kind"],
            "tone": TONE.get(p["key"], ""),
            "grade": grade,
            "text": text,
            "count": 1,
        }
        if "lat" in p:
            item["lat"], item["lng"] = p["lat"], p["lng"]
        out.append(item)
    return out
