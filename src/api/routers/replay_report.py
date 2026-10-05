"""replay_report.py — Behavior report for one played game, built on demand from stored replays."""

import json

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.analysis.replay_behavior import analyze_round, build_path, searched_polygons
from src.analysis.replay_narrative import narrate
from src.api.deps import get_ctx

router = APIRouter()

_QUERY = """
    SELECT r.round_number, r.score, r.distance_km, r.steps, r.time_sec,
           r.real_latitude, r.real_longitude, r.real_country_code, rc.name AS real_country,
           r.real_state, r.real_city,
           r.guess_latitude, r.guess_longitude, r.guess_country_code, gc.name AS guess_country,
           r.guess_state, r.guess_city,
           rp.events
    FROM round r
    JOIN game g ON g.challenge_token = r.challenge_token AND g.game_id = r.game_id
    LEFT JOIN round_replay rp
           ON rp.challenge_token = r.challenge_token
          AND rp.game_id = r.game_id
          AND rp.round_number = r.round_number
    LEFT JOIN country rc ON rc.code = r.real_country_code
    LEFT JOIN country gc ON gc.code = r.guess_country_code
    WHERE g.challenge_token = $1 AND g.game_id = $2
    ORDER BY r.round_number
"""


def _place(*parts) -> str:
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _round_payload(row: dict, locate, countries_in) -> dict:
    events = row["events"]
    if isinstance(events, str):
        events = json.loads(events)
    events = events or []
    real = {
        "lat": float(row["real_latitude"]),
        "lng": float(row["real_longitude"]),
        "country_code": (row["real_country_code"] or "").strip(),
        "country": row["real_country"] or "",
        "state": row["real_state"] or "",
        "city": row["real_city"] or "",
    }
    real["place"] = _place(real["city"], real["state"], real["country"])
    dist = float(row["distance_km"])
    phases = analyze_round(
        events, real, {"score": row["score"], "distance_km": dist}, locate, countries_in
    )
    guess = None
    if row["guess_latitude"] is not None:
        guess = {
            "lat": float(row["guess_latitude"]),
            "lng": float(row["guess_longitude"]),
            "place": _place(row["guess_city"], row["guess_state"], row["guess_country"]),
        }
    return {
        "round_number": row["round_number"],
        "score": row["score"],
        "distance_km": dist,
        "steps": row["steps"],
        "time_sec": row["time_sec"],
        "has_replay": bool(events),
        "real": {"lat": real["lat"], "lng": real["lng"], "place": real["place"]},
        "guess": guess,
        "timeline": narrate(phases),
        "path": build_path(events),
        "searched": searched_polygons(phases),
    }


@router.get("/replay-report/{challenge_token}/{game_id}")
async def replay_report(challenge_token: str, game_id: str, request: Request):
    ctx = get_ctx(request)
    local = ctx.local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    rows = await ctx.db_adapter.fetch_rows(_QUERY, challenge_token, game_id)
    if not rows:
        raise HTTPException(404, "Game not found")

    def locate(lat, lng):
        try:
            return local.lookup(lat, lng)["admin"]
        except Exception:  # noqa: BLE001
            return None

    def countries_in(west, south, east, north):
        try:
            return local.countries_in_bbox(west, south, east, north)
        except Exception:  # noqa: BLE001
            return []

    rounds = await run_in_threadpool(
        lambda: [_round_payload(r, locate, countries_in) for r in rows]
    )
    return {"rounds": rounds}
