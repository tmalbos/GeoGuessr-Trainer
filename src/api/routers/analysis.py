"""analysis.py — Filter options, available levels, and per-level zone analysis."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request

from src.analysis.confusion import confusion
from src.analysis.geo_levels import CHILD_LEVEL
from src.analysis.report import available_levels, build_groups, load_rounds
from src.analysis.scoring import score_tiers
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


async def _country_codes(request: Request) -> dict[str, str]:
    """Country name -> ISO code, so the UI can show flags next to country names."""
    rows = await get_ctx(request).db_adapter.fetch_rows("SELECT code, name FROM country")
    return {r["name"]: r["code"].strip() for r in rows}


def _with_codes(rows: list[dict], codes: dict[str, str]) -> list[dict]:
    return [
        {**r, "real_code": codes.get(r["real"]), "guess_code": codes.get(r["guess"])} for r in rows
    ]


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
        "tiers": score_tiers(),
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
    return [{"level": k, "label": label, "zones": n} for k, label, n in lv]


@router.get("/analysis/country/{code}")
async def country_analysis(
    code: str,
    request: Request,
    match_type: str = "daily",
    move_type: str = "moving",
    time_limit: str = "180",
):
    """One country's stats (for the Explore page). zone is null until it has enough rounds."""
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(422, "Invalid country code")
    db = get_ctx(request).db_adapter
    row = await db.fetch_one("SELECT name FROM country WHERE code = $1", code.upper())
    if not row:
        return {"total": 0, "zone": None}
    rounds = await load_rounds(db, match_type, move_type, _parse_time_limit(time_limit))
    name = row["name"]
    total = sum(1 for r in rounds if (r.get("real_geo") or {}).get("country") == name)
    stats = analyze(rounds, "country").zones.get(name)
    return {"total": total, "zone": asdict(stats) if stats else None}


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
    codes = await _country_codes(request) if "country" in {geo, child} else {}
    zones = []
    for name, s in result.zones.items():
        zr = groups.get(name, [])
        z = asdict(s) | {
            "name": name,
            "code": codes.get(name) if geo == "country" else None,
            "series": [r["distance_km"] for r in zr[-100:]],
            "confusions": [],
            "child_confusions": [],
        }
        if geo:
            top = confusion(zr, geo, top_n=1)
            z["confusions"] = _with_codes(top, codes) if geo == "country" else top
            inside = [r for r in zr if (r.get("real_geo") or {}).get(geo) == name]
            z["child_level"] = child
            sub = confusion(inside, child, top_n=3) if child else []
            z["child_confusions"] = _with_codes(sub, codes) if child == "country" else sub
        zones.append(z)
    zones.sort(key=lambda z: z["score_high"] if z["score_high"] is not None else 9999)
    return {"level": level, "label": result.level_label, "zones": zones}
