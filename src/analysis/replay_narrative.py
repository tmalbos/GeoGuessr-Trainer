"""replay_narrative.py — Layer 2: turn behavior phases into report sentences.

One template per phase key. A value is either a format string or a function of the phase
(for sentences that change with the facts or the grade). To add variety later, make a value a
list of templates and pick one (seed the choice by game + round so a report doesn't change on reload).
"""

# Adverbs by grade. Efficiency grades come from replay_grading.ratio_grade.
MOVE_ADVERB = {
    "best": "efficiently",
    "excellent": "",
    "good": "slowly",
    "inaccuracy": "sluggishly",
    "mistake": "very slowly",
    "blunder": "painfully slowly",
}
ZOOM_ADVERB = {
    "best": "",
    "excellent": "",
    "good": "slowly",
    "inaccuracy": "hesitantly",
    "mistake": "very slowly",
    "blunder": "agonizingly slowly",
}


def _dur(ms: float) -> str:
    s = round(ms / 1000)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def _km(d: float) -> str:
    return f"{d:.1f}" if d < 10 else f"{d:,.0f}"


def _secs(ms: float) -> str:
    return f"{int(ms // 1000):,}"


def _steps(n: int) -> str:
    return "one step" if n == 1 else f"{n} steps"


def _with(adverb: str) -> str:
    return f" {adverb}" if adverb else ""


# ── Movement ────────────────────────────────────────────────────────────────
def _move(c) -> str:
    head = "You closed the map and" if c.get("after_close") else "You"
    if c.get("back"):
        return f"{head} moved back {c['direction']} one step"
    return f"{head} advanced one step in the {c['direction']} direction"


def _run(c) -> str:
    verb = "started heading" if c.get("started") else "moved"
    return f"You {verb}{_with(MOVE_ADVERB.get(c['grade'], ''))} {c['direction']} for {c['steps']} steps"


def _erratic(c) -> str:
    tail = ", just to return to the same one you started out at" if c.get("loop") else ""
    return f"You moved erratically to {c['wasteful']} positions you've already been{tail}"


def _return_start(c) -> str:
    n = c["visit"]
    return "You went back to spawn" if n <= 2 else f"You went back to spawn once more (visit {n})"


# ── Map ─────────────────────────────────────────────────────────────────────
def _quick_map(c) -> str:
    if c["zoom_dir"]:
        return f"You opened and closed the map only doing a little bit of a zoom {c['zoom_dir']}"
    return "You opened and closed the map without really doing anything"


def _zoom_start(c) -> str:
    pre = "You opened the map and started zooming" if c.get("opened") else "You started zooming"
    return f"{pre}{_with(ZOOM_ADVERB.get(c['grade'], ''))} into {c['place']}"


def _zoom_in(c) -> str:
    place, level = c["place"], c.get("level")
    if c.get("grade") == "miss":
        if c.get("near_pin"):
            return (
                "You zoomed a little bit more around your current pin position, "
                "putting the real location out of view"
            )
        return f"You zoomed into {place}, putting the real location out of view"
    if c.get("hit"):
        if level == "country":
            return f"You zoomed straight into {place}, the country of the real location"
        if level == "region":
            named = "" if place.startswith("the ") else f", {place}"
            return f"You zoomed further into the region of the real location{named}"
        if level in {"city", "point"}:
            return (
                "You found the correct close area at which the real location is, and zoomed into it"
            )
    return f"You zoomed into {place}"


def _stare(c) -> str:
    place, secs = c["place"], _secs(c["dur_ms"])
    return {
        "": f"You spent {secs} seconds looking at {place}",
        "good": f"You lingered on {place} for {secs} seconds",
        "inaccuracy": f"You stared at {place} for {secs} solid seconds",
        "mistake": f"You stared at {place} for a long {secs} seconds",
        "blunder": f"You froze on {place} for {secs} seconds",
    }[c["grade"] if c["grade"] in {"good", "inaccuracy", "mistake", "blunder"} else ""]


def _align(c) -> str:
    where = " at spawn" if c.get("at_spawn") else ""
    nth, closed = c.get("nth", 1), c.get("after_close")
    if nth == 1:
        if closed:
            text = f"You closed the map in order to align the road{where}"
        elif c["key"] == "align_city":
            text = f"You identified this was {c['place']} and started trying to align the road"
        elif c["key"] == "align_region":
            text = f"You had narrowed it down to {c['place']} and started aligning the road"
        else:
            text = f"You aligned the road{where}"
    else:
        again = "yet once again" if nth == 2 else f"yet again (attempt {nth})"
        lead = "You closed the map and tried" if closed else "You tried"
        text = f"{lead} aligning the road{where} {again}"
    return text + (" and opened the map" if c.get("then_open") else "")


# ── Pins ────────────────────────────────────────────────────────────────────
_PIN_CLASS = {
    "pin_right_region": "the right region",
    "pin_wrong_region": "the wrong region",
    "pin_wrong_country": "the wrong country",
    "pin_off": "",
}


def _pin(c) -> str:
    key, prev = c["key"], c.get("prev_dist_km")
    moved = prev is not None
    if key == "pin_exact":
        return (
            "You moved your pin to the exact spot"
            if moved
            else f"You put a pin on the exact spot, in {c['place']}"
        )
    if key == "pin_5k":
        head = "You moved your pin to" if moved else "You put a pin on"
        return f"{head} the 5K area, although not precisely on the exact spot"
    km, place, cls = _km(c["dist_km"]), c["place"], _PIN_CLASS[key]
    miss = ", missing the real location" if c.get("grade") == "miss" else ""
    if not moved:
        if key == "pin_right_region":
            text = f"You placed a pin in {place}, the right region but {km} km off"
        elif cls:
            text = f"You placed a pin in {place}, {cls}, {km} km off"
        else:
            text = f"You placed a pin near {place}, {km} km from the real spot"
        return text + miss
    diff = abs(prev - c["dist_km"])
    if c["dist_km"] < prev - 0.05:
        rel = f"{_km(diff)} km closer than your previous pin, but still {km} km off from the real location"
    elif c["dist_km"] > prev + 0.05:
        rel = (
            f"{_km(diff)} km further than your previous pin, now {km} km off from the real location"
        )
    else:
        rel = f"about as far as your previous pin, {km} km off from the real location"
    cls_part = f", {cls}" if cls and key != "pin_right_region" else ""
    return f"You placed a pin in {place}{cls_part}, {rel}{miss}"


TEXT = {
    "start": "The round starts in {place}",
    "move": _move,
    "run": _run,
    "speed_move": "You speed-moved {steps} steps {direction}",
    "verify_move": lambda c: (
        f"You moved {_steps(c['steps'])} {c['direction']} in order to validate your guess"
    ),
    "erratic": _erratic,
    "move_revisit": "You went back to a position you had already been to",
    "return_start": _return_start,
    "pause": "You stopped to look for clues for {dur}",
    "map_open": "You opened the map for {dur}",
    "map_close": "You closed the map",
    "quick_map": _quick_map,
    "zoom_start": _zoom_start,
    "zoom_end": "You finished zooming into {place}",
    "zoom_in": _zoom_in,
    "zoom_out": "You zoomed out to {place}",
    "stare": _stare,
    "scan_country": "You seemed to know you were in {place}, but scanned across {n_regions} of its regions without a clear one in mind",
    "scan_country_wrong": "You scanned across {place} ({n_regions} regions), but the round was actually in {real_place}",
    "scan_multi_country": "You searched across several countries: {place}",
    "scan_regions_hit": "You started searching inside {place}, which included the right region",
    "scan_regions_miss": "You started searching inside {place}, but the real spot was in {real_place}",
    "scan_area": "You scanned the map around {place}",
    "align": _align,
    "align_city": _align,
    "align_region": _align,
    "pin_exact": _pin,
    "pin_5k": _pin,
    "pin_right_region": _pin,
    "pin_wrong_region": _pin,
    "pin_wrong_country": _pin,
    "pin_off": _pin,
    "verify_exact": "You had pinned the exact spot, but spent {dur} verifying it before confirming",
    "verify_other": "After pinning, you went back to street view for {dur} before confirming",
    "guess": "You confirmed your guess: {score} points",
    "no_guess": "The round ended without a guess",
}


def _note(p: dict) -> str:
    """An extra line for efficiency events: how much time was left on the table."""
    if p["key"] in {"run", "speed_move"} and p["grade"] in {
        "good",
        "inaccuracy",
        "mistake",
        "blunder",
    }:
        return f"If you had fast-moved, you could have saved about {(p['dur_ms'] - p['expected_ms']) / 1000:.1f}s"
    if p["key"] == "zoom_start":
        return (
            f"If you had properly zoomed into {p['place']}, "
            f"you could have saved about {(p['dur_ms'] - p['expected_ms']) / 1000:.1f}s"
        )
    return ""


def _text(p: dict, ctx: dict) -> str:
    template = TEXT[p["key"]]
    text = template(ctx) if callable(template) else template.format(**ctx)
    if p["kind"] in {"zoom", "scan"} and p["grade"] == "miss" and "out of view" not in text:
        text += ", leaving the real location out of view"
    return text


def _is_noise(p: dict, seen_scans: set) -> bool:
    """Events that say nothing worth a line: ungraded zooms, and ungraded searches that repeat an earlier one."""
    if p["key"] in {"zoom_in", "zoom_out"} and not p.get("grade"):
        return True
    if p["kind"] == "scan" and not p.get("grade"):
        sig = (p["key"], p.get("place"))
        if sig in seen_scans:
            return True
        seen_scans.add(sig)
    return False


def narrate(phases: list[dict]) -> list[dict]:
    """Phases -> [{t_ms, kind, grade, text, note, count, lat?, lng?}]. Identical neighbours are merged (count).

    `note` is extra info for the UI to show on hover only (how much time was left on the table).
    To give more events a note later (anything below "best"), extend _note().
    """
    out: list[dict] = []
    seen_scans: set = set()
    for p in phases:
        if _is_noise(p, seen_scans):
            continue
        ctx = {**p, "dur": _dur(p.get("dur_ms", 0)), "km": _km(p.get("dist_km", 0))}
        text = _text(p, ctx)
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
            "grade": grade,
            "text": text,
            "note": _note(p),
            "count": 1,
        }
        if "lat" in p:
            item["lat"], item["lng"] = p["lat"], p["lng"]
        out.append(item)
    return out
