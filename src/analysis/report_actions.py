"""report_actions.py — Up to two "what to study" actions for one game report.

Candidates, in tier order (first match wins a slot, duplicates are skipped, max MAX_ACTIONS):

  1. PINPOINT   every round is within PINPOINT_KM of the real spot, but not all are 5K.
  2. ERRORS     rounds with a guess that is in the wrong country (country action) or at least
                PINPOINT_KM off inside the right one (region action). Sorted by priority:
                    daily / challenge:  points lost                    (lowest score first)
                    duel:               points lost * share of that real country in your duel rounds
                Ties: country before region.
  3. REPLAYS    time spent looking at the wrong country (country action) or searching a country
                with no clear region in mind (region action), longest first (x frequency in duels).
                Only wasted time of MIN_WASTE_MS or more counts.

Clues come from data/clues (through `src`, see src/api/clue_source.py):
  country action  national clues of the real country that fit the real urban/rural type and are not a
                  "same as" of the guessed country, ranked by frequency + ease + reliability.
  region action   regional clues (polygons / CSV-derived polygons) of the country, ranked by
                  frequency + ease + reliability + AREA_WEIGHT * (area / biggest area).
                    guess pin:  clues whose polygon holds the real point but not the guess (fallback:
                                holds the guess but not the real point).
                    replay:     clues holding the real point whose area is <= half of the area searched.
No clues found = the sentence and the link are still returned, with an empty clue list.
"""

import logging

import geopandas as gpd
import shapely
from shapely.geometry import Point
from shapely.ops import unary_union

from src.analysis.replay_stages import PINPOINT_KM
from src.analysis.scoring import dist_to_score
from src.geo.admin_terms import with_term

logger = logging.getLogger("uvicorn.error")

PERFECT = 5000
PIN_SCORE = dist_to_score(PINPOINT_KM)  # score of a guess exactly PINPOINT_KM away
MAX_ACTIONS = 2
MAX_CLUES = 2
MIN_WASTE_MS = 20_000
AREA_WEIGHT = 10.0  # the biggest polygon adds this much (the three ratings add up to 30 at most)
SEARCH_AREA_SHARE = 0.5  # replay region clues must be this share of the searched area or smaller
INFO_MAX = 180
PINPOINT_URL: str | None = None  # where to send people to practise pinpointing (not decided yet)
PINPOINT_LABEL = "Practice pinpointing"

_LOC = {"urban": "Urban", "rural": "Rural"}


# ── helpers ────────────────────────────────────────────────────────────────


def area_km2(geom) -> float:
    if geom is None or geom.is_empty:
        return 0.0
    return float(gpd.GeoSeries([geom], crs=4326).to_crs(6933).area.iloc[0] / 1e6)


def _fmt_ms(ms: float) -> str:
    s = round(ms / 1000)
    return f"{s}s" if s < 60 else f"{s // 60}m {s % 60:02d}s"


def _rate(clue: dict) -> float:
    return sum(clue.get(k) or 0 for k in ("frequency", "ease", "reliability"))


def _loc_ok(clue: dict, area_type: str) -> bool:
    want = _LOC.get((area_type or "").lower())
    if want is None:
        return True
    loc = next((t for t in clue.get("tags", []) if t in {"Urban", "Rural", "Anywhere"}), None)
    return loc in {want, "Anywhere", None}


def _short(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= INFO_MAX else text[: INFO_MAX - 1].rstrip() + "…"


def _clue_out(cc: str, clue: dict) -> dict:
    return {
        "id": clue["id"],
        "cc": cc,
        "info": _short(clue.get("info", "")),
        "image": clue.get("image"),
        "tags": clue.get("tags", []),
    }


def _area_label(state: str, city: str, cc: str, country: str) -> str:
    name = with_term(state, cc) if state else city
    return ", ".join(p for p in (name, country) if p)


# ── clue selection ─────────────────────────────────────────────────────────


def national_clues(src, real_cc: str, other_cc: str, area_type: str) -> list[dict]:
    """National clues of the real country for this urban/rural type, minus the ones shared with `other_cc`."""
    out = []
    for c in src.clues(real_cc):
        if c.get("scope") != "nat" or not _loc_ok(c, area_type):
            continue
        shared = any(
            r.get("type") == "same_as" and str(r.get("cc", "")).upper() == other_cc.upper()
            for r in c.get("relations") or []
        )
        if other_cc and shared:
            continue
        out.append(c)
    out.sort(key=_rate, reverse=True)  # stable: draws keep their order in the file
    return out


def _rank_regional(pool: list[tuple[dict, float]]) -> list[dict]:
    if not pool:
        return []
    biggest = max(a for _, a in pool) or 1.0
    scored = [(_rate(c) + AREA_WEIGHT * a / biggest, c) for c, a in pool]
    scored.sort(key=lambda t: -t[0])
    return [c for _, c in scored]


def regional_clues(src, cc: str, area_type: str, real_pt, guess_pt) -> list[dict]:
    """Regional clues that tell the real region from the guessed one (fallback: the reverse)."""
    good, fallback = [], []
    for c in src.clues(cc):
        if c.get("scope") != "reg" or not _loc_ok(c, area_type):
            continue
        shape = src.shape(cc, c)
        if not shape:
            continue
        geom, area = shape
        has_real, has_guess = geom.intersects(real_pt), geom.intersects(guess_pt)
        if has_real and not has_guess:
            good.append((c, area))
        elif has_guess and not has_real:
            fallback.append((c, area))
    return _rank_regional(good or fallback)


def regional_clues_within(src, cc: str, area_type: str, real_pt, max_area: float) -> list[dict]:
    """Regional clues holding the real point whose polygon is no bigger than `max_area` (km²)."""
    pool = []
    for c in src.clues(cc):
        if c.get("scope") != "reg" or not _loc_ok(c, area_type):
            continue
        shape = src.shape(cc, c)
        if shape and shape[1] <= max_area and shape[0].intersects(real_pt):
            pool.append((c, shape[1]))
    return _rank_regional(pool)


# ── replay signals (per round, computed where the phases are) ──────────────


def replay_signals(phases: list[dict], real_code: str, locate) -> dict:
    """Time wasted searching, from the graded phases of one round.

    {"wrong": {key: {code, name, ms}}, "region_ms": ms, "region_area": km² of what was searched}
    wrong  = searches inside another country; region = searches in the right country with no clear region.
    """
    wrong: dict = {}
    region_ms, areas = 0, []
    for p in phases:
        if p["kind"] != "scan" or p["key"] == "scan_regions_hit":
            continue
        ms = int(p.get("dur_ms") or 0)
        a = locate(*p["center"]) if p.get("center") else None
        code = (a or {}).get("country_code") or ""
        key = p["key"]
        if key == "scan_country_wrong":
            name = p.get("place") or (a or {}).get("country") or code
        elif key == "scan_country" or (key == "scan_regions_miss" and code == real_code):
            region_ms += ms
            areas += [g for g in p.get("areas", []) if g is not None and not g.is_empty]
            continue
        elif code and code != real_code:
            name = (a or {}).get("country") or code
        else:
            continue
        slot = wrong.setdefault(code or name, {"code": code, "name": name, "ms": 0})
        slot["ms"] += ms
    area = 0.0
    if areas:
        try:
            area = area_km2(unary_union([shapely.make_valid(g) for g in areas]))
        except Exception:  # noqa: BLE001
            area = 0.0
    return {"wrong": wrong, "region_ms": region_ms, "region_area": area}


# ── candidates ─────────────────────────────────────────────────────────────


def _weight(r: dict, duel: bool, freq: dict) -> float:
    return freq.get(r["real"]["cc"], 0.0) if duel else 1.0


def _needs_pinpoint(rounds: list[dict]) -> bool:
    return (
        bool(rounds)
        and all(r["score"] >= PIN_SCORE for r in rounds)
        and not all(r["score"] >= PERFECT for r in rounds)
    )


def _error_candidates(rounds: list[dict], duel: bool, freq: dict) -> list[dict]:
    out = []
    for r in rounds:
        g = r["guess"]
        if not g:
            continue
        real_cc = r["real"]["cc"]
        wrong_country = bool(g["cc"] and real_cc and g["cc"] != real_cc)
        if not wrong_country and r["dist_km"] < PINPOINT_KM:
            continue
        lost = PERFECT - r["score"]
        out.append(
            {
                "kind": "country" if wrong_country else "region",
                "round": r,
                "lost": lost,
                "time_ms": None,
                "priority": lost * _weight(r, duel, freq),
                "other": {"cc": g["cc"], "country": g["country"]},
            }
        )
    out.sort(key=lambda c: (-c["priority"], c["kind"] != "country", c["round"]["n"]))
    return out


def _replay_candidates(rounds: list[dict], duel: bool, freq: dict) -> list[dict]:
    out = []
    for r in rounds:
        sig = r.get("signals") or {}
        w = _weight(r, duel, freq)
        out.extend(
            {
                "kind": "country",
                "round": r,
                "lost": None,
                "time_ms": info["ms"],
                "priority": info["ms"] * w,
                "other": {"cc": info["code"], "country": info["name"]},
            }
            for info in (sig.get("wrong") or {}).values()
            if info["ms"] >= MIN_WASTE_MS
        )
        if sig.get("region_ms", 0) >= MIN_WASTE_MS:
            out.append(
                {
                    "kind": "region",
                    "round": r,
                    "lost": None,
                    "time_ms": sig["region_ms"],
                    "priority": sig["region_ms"] * w,
                    "area": sig.get("region_area", 0.0),
                }
            )
    out.sort(key=lambda c: (-c["priority"], c["round"]["n"]))
    return out


def _key(c: dict) -> tuple:
    if c["kind"] == "pinpoint":
        return ("pinpoint",)
    real = c["round"]["real"]
    if c["kind"] == "country":
        return ("country", real["cc"], c["other"]["cc"] or c["other"]["country"])
    return ("region", real["cc"], real["state"] or real["city"])


# ── actions ────────────────────────────────────────────────────────────────


def _link(cc: str, scope: str, area_type: str, ranked: list[dict]) -> str:
    q = [f"scope={scope}"]
    if ranked:
        q.append("ids=" + ",".join(c["id"] for c in ranked))
    elif _LOC.get((area_type or "").lower()):
        q.append(f"loc={_LOC[area_type.lower()]}")
    return f"#/explore/{cc}/clues?" + "&".join(q)


def _action(c: dict, text: str, scope: str, ranked: list[dict]) -> dict:
    real = c["round"]["real"]
    loc = _LOC.get((real["area_type"] or "").lower())
    words = [
        loc,
        "national" if scope == "nat" else "regional",
        real["country"] or real["cc"],
        "clues",
    ]
    label = " ".join(w for w in words if w)
    return {
        "type": c["kind"],
        "round": c["round"]["n"],
        "text": text,
        "clues": [_clue_out(real["cc"], x) for x in ranked[:MAX_CLUES]],
        "link": _link(real["cc"], scope, real["area_type"], ranked),
        "link_label": label[0].upper() + label[1:],
    }


def _cost(c: dict) -> str:
    return (
        f"{c['lost']:,} points" if c["time_ms"] is None else f"{_fmt_ms(c['time_ms'])} of searching"
    )


def _country_action(c: dict, src) -> dict:
    real, other = c["round"]["real"], c["other"]
    ranked = national_clues(src, real["cc"], other["cc"], real["area_type"])
    text = (
        f"You confused {real['country'] or real['cc']} for "
        f"{other['country'] or other['cc']} and it cost you {_cost(c)}."
    )
    return _action(c, text, "nat", ranked)


def _region_action(c: dict, src) -> dict:
    r = c["round"]
    real, g = r["real"], r["guess"]
    cc, point = real["cc"], Point(real["lng"], real["lat"])
    x = _area_label(real["state"], real["city"], cc, real["country"])
    if g:  # a guess pin to compare against
        y = _area_label(g["state"], g["city"], cc, real["country"])
        if y and y != x:
            text = f"You confused {x} for {y} and it cost you {_cost(c)}."
        else:
            text = f"You guessed the wrong part of {x} and it cost you {_cost(c)}."
        ranked = regional_clues(src, cc, real["area_type"], point, Point(g["lng"], g["lat"]))
    else:  # no guess: only what the player was searching
        text = (
            f"You searched {real['country'] or cc} for {_fmt_ms(c['time_ms'])} without a clear "
            f"region in mind. The real spot was in {x}."
        )
        area = c.get("area") or 0.0
        ranked = (
            regional_clues_within(src, cc, real["area_type"], point, area * SEARCH_AREA_SHARE)
            if area > 0
            else []
        )
    return _action(c, text, "reg", ranked)


def _replay_region_action(c: dict, src) -> dict:
    """A replay region candidate: the round does have a guess, but it is the search that matters."""
    r = c["round"]
    real = r["real"]
    cc, point = real["cc"], Point(real["lng"], real["lat"])
    x = _area_label(real["state"], real["city"], cc, real["country"])
    text = (
        f"You searched {real['country'] or cc} for {_fmt_ms(c['time_ms'])} without a clear "
        f"region in mind. The real spot was in {x}."
    )
    area = c.get("area") or 0.0
    ranked = (
        regional_clues_within(src, cc, real["area_type"], point, area * SEARCH_AREA_SHARE)
        if area > 0
        else []
    )
    return _action(c, text, "reg", ranked)


def _pinpoint_action() -> dict:
    return {
        "type": "pinpoint",
        "round": None,
        "text": "Your guesses land in the right area, but not on the exact spot. "
        "Work on your pinpointing.",
        "clues": [],
        "link": PINPOINT_URL,
        "link_label": PINPOINT_LABEL,
    }


def _materialize(c: dict, src) -> dict:
    if c["kind"] == "pinpoint":
        return _pinpoint_action()
    if c["kind"] == "country":
        return _country_action(c, src)
    return _replay_region_action(c, src) if c["time_ms"] is not None else _region_action(c, src)


def build_actions(
    rounds: list[dict], src, duel: bool = False, freq: dict | None = None
) -> list[dict]:
    """rounds: [{n, score, dist_km, real{lat,lng,cc,country,state,city,area_type}, guess{...}|None, signals}]."""
    freq = freq or {}
    cands: list[dict] = []
    if _needs_pinpoint(rounds):
        cands.append({"kind": "pinpoint"})
    cands += _error_candidates(rounds, duel, freq)
    cands += _replay_candidates(rounds, duel, freq)
    chosen, seen = [], set()
    for c in cands:
        k = _key(c)
        if k in seen:
            continue
        seen.add(k)
        chosen.append(c)
        if len(chosen) == MAX_ACTIONS:
            break
    out = []
    for c in chosen:
        try:
            out.append(_materialize(c, src))
        except Exception:
            logger.exception("Could not build a report action")
    return out
