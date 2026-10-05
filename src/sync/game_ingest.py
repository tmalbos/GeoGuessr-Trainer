"""game_ingest.py — Enrich, compute replay-derived metrics, and persist one game."""

import asyncio

from src.anki.anki_connect import AnkiConnectClient
from src.db.repositories.game_repository import GameRepository
from src.geo.enrichment_service import GeoEnrichClient
from src.geoguessr.client import GeoguessrClient
from src.geoguessr.normalize import infer_move_type_from_replays, move_type_from_challenge
from src.geoguessr.replay import compress_replay, steps_from_replay, time_sec_from_replay
from src.shared.events import Emit, Event, noop


async def process_game(
    normalized_rounds: list[dict],
    game_id: str,
    challenge_token: str,
    match_type: str,
    map_name: str,
    played_at: str | None,
    time_limit_sec: int | None,
    db: GameRepository,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    client: GeoguessrClient,
    user_id: str,
    forbid_flags: dict | None = None,
    emit: Emit = noop,
) -> list[str]:
    """Enrich, save and generate cards for one game. Returns Anki errors."""
    if not normalized_rounds:
        await emit(Event("log", {"message": "No rounds found in this game.", "level": "warn"}))
        return []

    await emit(Event("game_started", {"game_id": game_id, "map_name": map_name}))

    real_enriched, guess_enriched = await asyncio.gather(
        geo_client.enrich_all([(r["real_lat"], r["real_lng"]) for r in normalized_rounds]),
        geo_client.enrich_all([(r["guess_lat"], r["guess_lng"]) for r in normalized_rounds]),
    )

    is_duel = match_type == "duel"
    # Plain challenges (not the daily one) have no replays on GeoGuessr's side:
    # don't fetch them and don't store empty round_replay rows.
    has_replays = match_type != "challenge"

    if has_replays:
        raw_replays = await asyncio.gather(
            *[client.fetch_replay(user_id, game_id, r["round_number"]) for r in normalized_rounds],
        )
    else:
        raw_replays = [[] for _ in normalized_rounds]

    rounds_to_save = []
    compressed_replays = []
    total_score = 0
    total_dist_km = 0.0

    for i, r in enumerate(normalized_rounds):
        compressed = compress_replay(raw_replays[i])
        compressed_replays.append(compressed)

        # Duels expose no steps/time, so they come from the replay.
        # Dailies/challenges report both in the game payload.
        if is_duel:
            steps = steps_from_replay(compressed)
            time_sec = time_sec_from_replay(compressed)
        else:
            steps = r["steps"] or 0
            time_sec = r["time_sec"] or 0

        row = {
            "game_id": game_id,
            "round_number": r["round_number"],
            "real_geo": real_enriched[i],
            "guess_geo": guess_enriched[i],
            "score": r["score"],
            "distance_km": r["distance_km"],
            "steps": steps,
            "time_sec": time_sec,
            "replay": compressed if has_replays else None,
        }
        total_score += r["score"] or 0
        total_dist_km += r["distance_km"] or 0.0
        rounds_to_save.append(row)
        await emit(Event("round_result", row))

    move_type = (
        infer_move_type_from_replays(compressed_replays)
        if is_duel
        else move_type_from_challenge(forbid_flags or {})
    )

    n = len(normalized_rounds)
    await db.save_game(
        {
            "game_id": game_id,
            "challenge_token": challenge_token,
            "match_type": match_type,
            "round_count": n,
            "time_limit_sec": time_limit_sec,
            "move_type": move_type,
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
