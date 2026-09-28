"""analysis.py — Filter options, available levels, and per-level zone analysis."""

from dataclasses import asdict

from fastapi import APIRouter, Request

from src.analysis.confusion import confusion
from src.analysis.geo_levels import CHILD_LEVEL
from src.analysis.report import available_levels, build_groups, load_rounds
from src.analysis.zone_stats import analyze
from src.api.deps import get_ctx

MIN_ROUNDS = 10
DEFAULT_FILTERS = {"match_type": "daily", "move_type": "moving", "time_limit_sec": 180}

router = APIRouter()


def _parse_time_limit(value: str) -> int | None:
    """'null' (or empty) means no time limit; anything else parses to seconds."""
    if not value or value.lower() == "null":
        return None
    return int(value)


@router.get("/analysis/filters")
async def analysis_filters(request: Request):
    combos = await get_ctx(request).db_adapter.fetch_analysis_filter_options()
    return {
        "match_types": sorted({c["match_type"] for c in combos}),
        "move_types": sorted({c["move_type"] for c in combos}),
        "time_limits": sorted(
            {c["time_limit_sec"] for c in combos},
            key=lambda v: (v is None, v),
        ),
        "combos": combos,
        "default": DEFAULT_FILTERS,
    }


@router.get("/analysis/levels")
async def levels(
    request: Request,
    match_type: str = "daily",
    move_type: str = "moving",
    time_limit: str = "180",
):
    tl = _parse_time_limit(time_limit)
    lv = await available_levels(get_ctx(request).db_adapter, MIN_ROUNDS, match_type, move_type, tl)
    return [{"level": k, "label": label, "rounds": n} for k, label, n in lv]


# Must stay AFTER /analysis/filters and /analysis/levels, or "{level}" swallows them.
@router.get("/analysis/{level}")
async def analysis(
    level: str,
    request: Request,
    match_type: str = "daily",
    move_type: str = "moving",
    time_limit: str = "180",
):
    tl = _parse_time_limit(time_limit)
    rounds = await load_rounds(get_ctx(request).db_adapter, match_type, move_type, tl)
    geo = None if level == "general" else level
    result = analyze(rounds, geo)
    groups = build_groups(rounds, geo)
    child = CHILD_LEVEL.get(level)
    zones = []
    for name, s in result.zones.items():
        zr = groups.get(name, [])
        z = asdict(s) | {
            "name": name,
            "series": [r["distance_km"] for r in zr[-100:]],
            "confusions": [],
            "child_confusions": [],
        }
        if geo:
            z["confusions"] = confusion(zr, geo, top_n=1)
            inside = [r for r in zr if (r.get("real_geo") or {}).get(geo) == name]
            z["child_level"] = child
            z["child_confusions"] = confusion(inside, child, top_n=3) if child else []
        zones.append(z)
    zones.sort(key=lambda z: z["score_high"] if z["score_high"] is not None else 9999)
    return {"level": level, "label": result.level_label, "zones": zones}
