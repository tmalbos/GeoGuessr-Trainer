import asyncio

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.core.events import Emit, Event, noop
from src.core.geo_enrich import GeoEnrichClient
from src.db.db import DbAdapter


async def process_game(
    game_data: dict,
    game_id: str,
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
    emit: Emit = noop,
) -> list[str]:
    """Enrich, save and generate cards for one game. Returns Anki errors."""
    rounds_raw = game_data.get("rounds", [])
    guesses = game_data.get("player", {}).get("guesses", [])
    map_name = game_data.get("mapName", "unknown")
    played_at = rounds_raw[0].get("startTime") if rounds_raw else None

    if not rounds_raw:
        await emit(Event("log", {"message": "No rounds found in this game.", "level": "warn"}))
        return []

    await emit(Event("game_started", {"game_id": game_id, "map_name": map_name}))

    real_enriched, guess_enriched = await asyncio.gather(
        geo_client.enrich_all([(r["lat"], r["lng"]) for r in rounds_raw]),
        geo_client.enrich_all([(g.get("lat"), g.get("lng")) for g in guesses]),
    )

    rounds_to_save = []
    total_score = 0
    total_dist_km = 0.0

    for i, guess in enumerate(guesses[: len(rounds_raw)], start=1):
        score_pts = guess.get("roundScoreInPoints", 0)
        dist_km = (
            round(guess["distanceInMeters"] / 1000, 1) if guess.get("distanceInMeters") else None
        )
        row = {
            "game_id": game_id,
            "round_number": i,
            "real_geo": real_enriched[i - 1],
            "guess_geo": guess_enriched[i - 1],
            "score": score_pts,
            "distance_km": dist_km,
            "steps": guess.get("stepsCount", 0),
            "time_sec": guess.get("time"),
        }
        total_score += score_pts or 0
        total_dist_km += dist_km or 0.0
        rounds_to_save.append(row)
        await emit(Event("round_result", row))

    n = len(guesses)
    await db.save_game(
        {
            "game_id": game_id,
            "challenge_token": game_data.get("challenge_token"),
            "is_daily": game_data.get("is_daily"),
            "played_at": played_at,
            "map_name": map_name,
            "rounds": rounds_to_save,
        },
    )
    await emit(
        Event(
            "game_done",
            {
                "game_id": game_id,
                "rounds": n,
                "total_score": total_score,
                "avg_distance_km": round(total_dist_km / n, 1) if n else 0,
            },
        ),
    )

    return []
    # return await generate_cards_for_game(
    #     rounds_to_save,
    #     db=db,
    #     anki_client=anki_client,
    #     http_client=http_client,
    # )
