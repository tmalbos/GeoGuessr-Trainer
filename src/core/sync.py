"""sync.py — Pipeline async: fetch → enrich+save → anki. Emits structured events."""

import asyncio
import os

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.core.analyzer import process_game
from src.core.api import GeoguessrClient
from src.core.events import Emit, Event, noop
from src.core.geo_enrich import GeoEnrichClient
from src.db.db import DbAdapter

DEFAULT_USER_ID = "68daf785000ba2a268744f99"
_SENTINEL = None


def _user_id() -> str:
    return os.environ.get("GEOGUESSR_USER_ID") or DEFAULT_USER_ID


async def _log(emit: Emit, message: str, level: str = "info") -> None:
    await emit(Event("log", {"message": message, "level": level}))


async def _fetch_worker(
    client: GeoguessrClient,
    entries: list[dict],
    queue: asyncio.Queue,
    emit: Emit,
) -> None:
    try:
        for entry in entries:
            token = entry["challenge_token"]
            if entry["is_daily"]:
                game_token = await client.fetch_daily_game_token(entry["date_str"], _user_id())
            else:
                game_token = await client.fetch_game_token(token)

            if not game_token:
                await _log(emit, f"[{token}] No game token.", "warn")
                continue
            try:
                game_data = await client.fetch_game(game_token)
            except Exception as e:  # noqa: BLE001
                await _log(emit, f"[{game_token}] {e}", "error")
                continue

            game_data["challenge_token"] = token
            game_data["is_daily"] = entry["is_daily"]
            await queue.put((game_token, game_data))
    finally:
        await queue.put(_SENTINEL)


async def _process_worker(
    queue: asyncio.Queue,
    anki_errors: list[str],
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
    emit: Emit,
) -> None:
    while (item := await queue.get()) is not _SENTINEL:
        game_token, game_data = item
        anki_errors.extend(
            await process_game(
                game_data,
                game_token,
                db=db,
                geo_client=geo_client,
                anki_client=anki_client,
                http_client=http_client,
                emit=emit,
            ),
        )


async def sync_from_feed(
    client: GeoguessrClient,
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
    emit: Emit = noop,
) -> None:
    await _log(emit, "Fetching feed...")
    entries = await client.fetch_feed_entries()
    saved = await db.fetch_saved_challenge_tokens([e["challenge_token"] for e in entries])
    new_entries = [e for e in entries if e["challenge_token"] not in saved]
    await emit(Event("feed", {"found": len(entries), "new": len(new_entries)}))

    if not new_entries:
        await _log(emit, "Up to date, no new games.", "success")
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    anki_errors: list[str] = []
    await asyncio.gather(
        _fetch_worker(client, new_entries, queue, emit),
        _process_worker(queue, anki_errors, db, geo_client, anki_client, http_client, emit),
    )
    if anki_errors:
        await emit(Event("anki_errors", {"errors": anki_errors}))
