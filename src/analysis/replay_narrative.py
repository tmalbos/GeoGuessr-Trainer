"""replay_narrative.py — Layer 2: turn behavior phases into report sentences.

One template per phase key. A value is either a format string or a function of the phase
(for sentences that change with the facts or the grade). To add variety later, make a value a
list of templates and pick one (seed the choice by game + round so a report doesn't change on reload).
"""

from src.geo.admin_terms import UNKNOWN_PLACE

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
# Adverbs that go BEFORE the verb ("slowly moved", "efficiently moved"). Any other adverb
# ("very slowly", "painfully slowly"...) goes right after the verb ("moved painfully slowly").
PRE_ADVERBS = {"slowly", "efficiently", "hesitantly", "sluggishly"}
POSITIVE = {"best", "excellent", "good"}
# Walking runs only show a grade badge when they were bad; a good run just carries its adverb.
UNBADGED_WHEN_GOOD = {"run", "speed_move"}
# Slow enough to count as time that could have been saved.
SAVING_GRADES = {"good", "inaccuracy", "mistake", "blunder"}
# Zoom: the place it went to is "bad" with these grades; the speed is "good" with these.
BAD_ZOOM_GRADES = {"inaccuracy", "miss", "mistake", "blunder"}
GOOD_SPEED_GRADES = {"best", "excellent"}


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


def _or(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]


def _verb(verb: str, adverb: str) -> tuple[str, str]:
    """(verb with a leading adverb, text to put after the verb): 'slowly moved' / 'moved very slowly'."""
    if adverb in PRE_ADVERBS:
        return f"{adverb} {verb}", ""
    return verb, _with(adverb)


# ── Start ───────────────────────────────────────────────────────────────────
def _start(c) -> str:
    country = c.get("country") or UNKNOWN_PLACE
    if c.get("area") == "urban":
        head = f"The round starts in urban {country}"
        return f"{head}, inside of {c['admin']}" if c.get("admin") else head
    if c.get("area") == "rural":
        head = f"The round starts in rural {country}"
        return f"{head}, around {c['around']}" if c.get("around") else head
    return f"The round starts in {c['place']}"


# ── Movement ────────────────────────────────────────────────────────────────
def _move(c) -> str:
    head = "You closed the map and" if c.get("after_close") else "You"
    tail = " while opening the map" if c.get("while_open") else ""
    if c.get("back"):
        return f"{head} moved back {c['direction']} one step{tail}"
    return f"{head} advanced one step in the {c['direction']} direction{tail}"


def _run(c) -> str:
    verb, after = _verb(
        "started heading" if c.get("started") else "moved", MOVE_ADVERB.get(c["grade"], "")
    )
    return f"You {verb}{after} {c['direction']} for {c['steps']} steps"


def _erratic(c) -> str:
    tail = ", just to return to the same one you started out at" if c.get("loop") else ""
    return f"You moved erratically to {c['wasteful']} positions you've already been{tail}"


def _return_start(c) -> str:
    n = c["visit"]
    return "You went back to spawn" if n <= 2 else f"You went back to spawn once more (visit {n})"


# ── Map ─────────────────────────────────────────────────────────────────────
def _quick_map(c) -> str:
    if c["zoom_dir"]:
        return f"You opened the map and closed it only doing a little bit of a zoom {c['zoom_dir']}"
    return "You opened and closed the map without really doing anything"


def _zoom_in(c) -> str:
    text = _zoom_in_core(c)
    if c.get("search_tail"):
        text += f", and ended up searching inside of it for {c['search_tail']}"
    return text


def _zoom_in_core(c) -> str:
    """WHERE the zoom went. A bad zoom also carries the speed adverb, but only when the speed was bad too."""
    place, level = c["place"], c.get("level")
    verb, after = _verb("zoomed back" if c.get("revisit") else "zoomed", c.get("speed_adverb", ""))
    head = "You opened the map and" if c.get("opened") else "You"
    base = f"{head} {verb}{after} into {place}"
    if c.get("grade") == "miss":
        if c.get("near_pin"):
            return (
                "You zoomed a little bit more around your current pin position, "
                "putting the real location out of view"
            )
        return f"{base}, putting the real location out of view"
    if c.get("hit") and not c.get("opened"):
        if level == "country":
            return f"You zoomed straight into {place}, the country of the real location"
        if level == "region":
            named = "" if place.startswith("the ") else f", {place}"
            return f"You zoomed further into the region of the real location{named}"
        if level in {"city", "point"}:
            return (
                "You found the correct close area at which the real location is, and zoomed into it"
            )
    return base


def _zoom_speed(c) -> str:
    """HOW FAST the zoom was, on its own line with its own grade."""
    g = c["grade"]
    if g in GOOD_SPEED_GRADES:
        return f"And you did it {'efficiently' if g == 'best' else 'quickly'}"
    return f"Although you did it {ZOOM_ADVERB.get(g) or 'slowly'}"


def _zoom_out(c) -> str:
    if c["place"] == UNKNOWN_PLACE or c["place"].startswith(("a wide", "the world")):
        return "You zoomed back to a wide area"
    return f"You zoomed out to {c['place']}"


def _stare(c) -> str:
    place, secs = c["place"], _secs(c["dur_ms"])
    return {
        "": f"You spent {secs} seconds looking at {place}",
        "good": f"You lingered on {place} for {secs} seconds",
        "inaccuracy": f"You stared at {place} for {secs} solid seconds",
        "mistake": f"You stared at {place} for a long {secs} seconds",
        "blunder": f"You froze on {place} for {secs} seconds",
    }[c["grade"] if c["grade"] in {"good", "inaccuracy", "mistake", "blunder"} else ""]


def _hesitate(c) -> str:
    if c["first"]:
        return (
            f"You started hesitating about whether this was {_or([c['prev'], c['name']])}, "
            f"making you change your focus to {c['name']}"
        )
    if c["back"]:
        return f"You started hesitating again and zoomed back to {c['name']}"
    return (
        f"You started hesitating again about whether this was {_or(c['others'])}, "
        f"making you change your focus to {c['name']}"
    )


def _scan_country(c) -> str:
    if c["key"] == "scan_country":
        if c.get("parts"):
            return (
                f"You seemed to know you were in {c['place']}, but searched around its "
                f"{c['parts']} regions for {c['dur']} without a clear one in mind"
            )
        return (
            f"You seemed to know you were in {c['place']}, but scanned across "
            f"{c['n_regions']} of its regions without a clear one in mind"
        )
    if c.get("parts"):
        return (
            f"You zoomed further into {c['place']} and started searching around its "
            f"{c['parts']} regions for {c['dur']}"
        )
    return (
        f"You zoomed further into {c['place']} and started searching across "
        f"{c['n_regions']} of its regions for {c['dur']}"
    )


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
    # Close pin (right region by distance) that sits across an admin line or a border.
    wrong_side = {
        "admin": f"the wrong {c.get('term', 'region')}",
        "country": "the wrong country",
    }.get(c.get("diff", ""))
    if not moved:
        if key == "pin_right_region" and wrong_side:
            text = f"You placed a pin in {place}, the right region, although {km} km off and in {wrong_side}"
        elif key == "pin_right_region":
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
    extra = f", and in {wrong_side}" if key == "pin_right_region" and wrong_side else ""
    return f"You placed a pin in {place}{cls_part}, {rel}{extra}{miss}"


def _guess(c) -> str:
    if c.get("timed_out"):
        return f"Time ran out: {c['score']} points"
    return f"You confirmed your guess: {c['score']} points"


TEXT = {
    "start": _start,
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
    "zoom_in": _zoom_in,
    "zoom_speed": _zoom_speed,
    "zoom_out": _zoom_out,
    "stare": _stare,
    "hesitate": _hesitate,
    "scan_country": _scan_country,
    "scan_country_wrong": _scan_country,
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
    "guess": _guess,
    "no_guess": "The round ended without a guess",
}


def saved_ms(p: dict) -> float:
    """Time that efficient movement / zooming would have saved on this event (0 if it was fine)."""
    if p["key"] in {"run", "speed_move", "zoom_speed"} and p["grade"] in SAVING_GRADES:
        return max(0.0, p["dur_ms"] - p["expected_ms"])
    return p.get("extra_saved", 0.0)  # a bad zoom that carries its own (slow) speed in the sentence


def _note(p: dict) -> str:
    """An extra line for efficiency events: how much time was left on the table."""
    ms = saved_ms(p)
    if not ms:
        return ""
    if p["key"] in {"run", "speed_move"}:
        return f"If you had fast-moved, you could have saved about {ms / 1000:.1f}s"
    return (
        f"If you had properly zoomed into {p['place']}, you could have saved about {ms / 1000:.1f}s"
    )


def _text(p: dict, ctx: dict) -> str:
    template = TEXT[p["key"]]
    text = template(ctx) if callable(template) else template.format(**ctx)
    if p["kind"] in {"zoom", "scan"} and p["grade"] == "miss" and "out of view" not in text:
        text += ", leaving the real location out of view"
    return text


def _pair_zooms(phases: list[dict]) -> None:
    """Decide which zoom events are told and how, from the grade of WHERE it went and HOW FAST.

    Told:
      good zoom + any speed  -> the zoom line; a speed line only if the speed was bad, or if it is
                                the zoom that opens the map ("And you did it efficiently").
                                A good zoom into a place already zoomed into earlier is skipped
                                (unless it opens the map).
      bad zoom + bad speed   -> one line with the speed adverb inside the sentence.
      bad zoom + good speed  -> one line, unless a hesitation / search line already tells it.
      no grade + good speed  -> nothing.
    An unknown-country search that follows the zoom is folded into the zoom line.
    """
    speed = {p["zid"]: p for p in phases if p["key"] == "zoom_speed"}
    told = [p for p in phases if p["kind"] in {"hesitate", "scan"}]
    for p in phases:
        if p["key"] != "zoom_in":
            continue
        sp = speed.get(p["zid"])
        end_t = sp["t"] if sp else p["t"]
        grade = p.get("grade") or ""
        loc_pos = grade in POSITIVE
        loc_bad = grade in BAD_ZOOM_GRADES
        speed_bad = bool(sp) and sp["grade"] not in GOOD_SPEED_GRADES
        covered = any(
            q["t"] - 1500 <= end_t and q["t"] + q.get("dur_ms", 0) >= p["t"] for q in told
        )
        hide = False
        if not speed_bad:
            if loc_pos:
                hide = bool(p.get("revisit")) and not p.get("opened")
            else:
                hide = (not loc_bad) or covered
        if hide:
            p["hide"] = True
            if sp:
                sp["hide"] = True
            continue
        if sp and (loc_bad or (not speed_bad and not p.get("opened"))):
            sp["hide"] = True
        if sp and loc_bad and speed_bad:
            p["speed_adverb"] = ZOOM_ADVERB.get(sp["grade"], "")
            p["extra_saved"] = saved_ms(sp)
    # "...zoomed into Unknown_Country, and ended up searching inside of it for 8s"
    for q in phases:
        if q["kind"] != "scan" or q["key"] != "scan_area":
            continue
        for p in phases:
            sp = speed.get(p.get("zid")) if p["key"] == "zoom_in" else None
            if sp and not p.get("hide") and -500 <= q["t"] - sp["t"] <= 3000:
                p["search_tail"] = _dur(q.get("dur_ms", 0))
                q["hide"] = True
                break


def _is_noise(p: dict, seen_scans: set) -> bool:
    """Events that say nothing worth a line."""
    if p.get("hide"):
        return True
    if p["key"] == "zoom_out":
        # A zoom out only matters when it goes wrong: the real spot leaves the view, the view lands
        # in another country, or the player is hesitating.
        return not (
            p.get("grade") or not p.get("hit") or p.get("other_country") or p.get("hesitating")
        )
    if p["key"] == "scan_regions_hit":
        return True  # searching inside the right country's few regions: the zooms already say it
    if p["kind"] == "scan" and not p.get("grade"):
        sig = (p["key"], p.get("place"))
        if sig in seen_scans:
            return True
        seen_scans.add(sig)
    return False


def narrate(phases: list[dict]) -> list[dict]:
    """Phases -> [{t_ms, kind, grade, text, note, count, saved_ms, lat?, lng?}]. Identical neighbours are merged (count).

    `note` is extra info for the UI to show on hover only (how much time was left on the table).
    `saved_ms` is the time efficient movement / zooming would have saved on that event.
    """
    out: list[dict] = []
    seen_scans: set = set()
    _pair_zooms(phases)
    for p in phases:
        if _is_noise(p, seen_scans):
            continue
        ctx = {**p, "dur": _dur(p.get("dur_ms", 0)), "km": _km(p.get("dist_km", 0))}
        text = _text(p, ctx)
        grade = p.get("grade") or ""
        if p["key"] in UNBADGED_WHEN_GOOD and grade in POSITIVE:
            grade = ""  # the adverb already says it; no badge for fine movement
        saved = saved_ms(p)
        if (
            out
            and out[-1]["kind"] == p["kind"]
            and out[-1]["text"] == text
            and out[-1]["grade"] == grade
        ):
            out[-1]["count"] += 1
            out[-1]["saved_ms"] += saved
            continue
        item = {
            "t_ms": p["t"],
            "kind": p["kind"],
            "grade": grade,
            "text": text,
            "note": _note(p),
            "count": 1,
            "saved_ms": saved,
        }
        if "lat" in p:
            item["lat"], item["lng"] = p["lat"], p["lng"]
        out.append(item)
    return out
