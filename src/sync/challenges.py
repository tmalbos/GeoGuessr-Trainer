"""challenges.py — Daily/challenge games: fetch (producer) and process (consumer) steps."""

import asyncio

from src.anki.anki_connect import AnkiConnectClient
from src.db.repositories.game_repository import GameRepository
from src.geo.enrichment_service import GeoEnrichClient
from src.geoguessr.client import GeoguessrClient
from src.geoguessr.normalize import normalize_challenge_rounds
from src.shared.events import Emit, log
from src.sync.game_ingest import process_game
from src.sync.user_identity import user_id

SENTINEL = None


async def fetch_challenge_worker(
    client: GeoguessrClient,
    entries: list[dict],
    queue: asyncio.Queue,
    emit: Emit,
) -> None:
    try:
        for entry in entries:
            token = entry["challenge_token"]
            game_token = entry.get("game_token")
            if not game_token:
                if entry["is_daily"]:
                    game_token = await client.fetch_daily_game_token(entry["date_str"], user_id())
                else:
                    game_token = await client.fetch_game_token(token)

            if not game_token:
                await log(emit, f"[{token}] No game token.", "warn")
                continue
            try:
                game_data = await client.fetch_game(game_token)
            except Exception as e:  # noqa: BLE001
                await log(emit, f"[{game_token}] {e}", "error")
                continue

            match_type = "daily" if entry["is_daily"] else "challenge"
            await queue.put((token, game_token, match_type, game_data))
    finally:
        await queue.put(SENTINEL)


async def process_challenge(
    client: GeoguessrClient,
    token: str,
    game_token: str,
    match_type: str,
    game_data: dict,
    db: GameRepository,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    emit: Emit,
) -> list[str]:
    normalized = normalize_challenge_rounds(game_data)
    forbid_flags = {
        k: game_data[k]
        for k in ("forbidMoving", "forbidZooming", "forbidRotating")
        if k in game_data
    }
    played_at = game_data["rounds"][0].get("startTime") if game_data.get("rounds") else None

    return await process_game(
        normalized,
        game_id=game_token,
        challenge_token=token,
        match_type=match_type,
        map_name=game_data["mapName"],
        played_at=played_at,
        time_limit_sec=game_data["timeLimit"],
        db=db,
        geo_client=geo_client,
        anki_client=anki_client,
        client=client,
        user_id=user_id(),
        forbid_flags=forbid_flags,
        emit=emit,
    )
