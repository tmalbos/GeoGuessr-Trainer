"""orchestrator.py — Pipeline async: fetch → enrich+save → anki. Emits structured events."""

import asyncio

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.db.repositories.game_repository import GameRepository
from src.geo.enrichment_service import GeoEnrichClient
from src.geoguessr.client import GeoguessrClient
from src.shared.events import Emit, Event, log, noop
from src.sync.challenges import SENTINEL, fetch_challenge_worker, process_challenge
from src.sync.duels import fetch_new_duels, process_duel


async def sync_from_feed(
    client: GeoguessrClient,
    db: GameRepository,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
    match_types: set[str] | None = None,
    emit: Emit = noop,
) -> None:
    """Sync new games for the requested match types (default: all three)."""
    match_types = match_types or {"daily", "challenge", "duel"}
    anki_errors: list[str] = []

    if "daily" in match_types or "challenge" in match_types:
        await log(emit, "Fetching feed...")
        entries = await client.fetch_feed_entries()
        entries = [e for e in entries if ("daily" if e["is_daily"] else "challenge") in match_types]
        saved = await db.fetch_saved_challenge_tokens([e["challenge_token"] for e in entries])
        new_entries = [e for e in entries if e["challenge_token"] not in saved]
        await emit(Event("feed", {"found": len(entries), "new": len(new_entries)}))

        if new_entries:
            queue: asyncio.Queue = asyncio.Queue(maxsize=4)

            async def _drain() -> None:
                while (item := await queue.get()) is not SENTINEL:
                    token, game_token, match_type, game_data = item
                    anki_errors.extend(
                        await process_challenge(
                            client,
                            token,
                            game_token,
                            match_type,
                            game_data,
                            db,
                            geo_client,
                            anki_client,
                            emit,
                        ),
                    )

            await asyncio.gather(
                fetch_challenge_worker(client, new_entries, queue, emit),
                _drain(),
            )
        else:
            await log(emit, "No new daily/challenge games.", "success")

    if "duel" in match_types:
        await log(emit, "Fetching duel history...")
        new_duels = await fetch_new_duels(client, db)
        await emit(Event("feed", {"found": len(new_duels), "new": len(new_duels)}))
        for duel in new_duels:
            anki_errors.extend(
                await process_duel(client, duel, db, geo_client, anki_client, emit),
            )

    if anki_errors:
        await emit(Event("anki_errors", {"errors": anki_errors}))
