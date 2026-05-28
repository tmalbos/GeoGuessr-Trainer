"""sync.py — Pipeline async: fetch → enrich+save → anki."""

import asyncio

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.core.analyzer import process_game
from src.core.api import GeoguessrClient
from src.core.geo_enrich import GeoEnrichClient
from src.db.db import DbAdapter
from src.i18n.lang import translate

USER_ID = "68daf785000ba2a268744f99"

_SENTINEL = None


async def _fetch_worker(client: GeoguessrClient, entries: list[dict], queue: asyncio.Queue) -> None:
    """Resuelve game tokens y encola (entry, game_data)."""
    for entry in entries:
        challenge_token = entry["challenge_token"]
        is_daily = entry["is_daily"]
        date_str = entry["date_str"]

        if is_daily:
            game_token = await client.fetch_daily_game_token(date_str, USER_ID)
        else:
            game_token = await client.fetch_game_token(challenge_token)

        if not game_token:
            print(translate("  ⚠️  [{token}] No game token.", token=challenge_token))
            continue

        try:
            game_data = await client.fetch_game(game_token)
        except ValueError as e:
            print(translate("  ❌ [{token}] Not found: {e}", token=game_token, e=e))
            continue
        except Exception as e:
            print(translate("  ❌ [{token}] Unexpected error: {e}", token=game_token, e=e))
            continue

        game_data["challenge_token"] = challenge_token
        game_data["is_daily"] = is_daily
        await queue.put((game_token, game_data))

    await queue.put(_SENTINEL)


async def _process_worker(
    queue: asyncio.Queue,
    anki_errors: list[str],
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
) -> None:
    """Consume la queue, enriquece, guarda y genera cards."""
    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break
        game_token, game_data = item
        print(translate("  🔍 [{token}] Processing...", token=game_token))
        errors = await process_game(
            game_data,
            game_token,
            db=db,
            geo_client=geo_client,
            anki_client=anki_client,
            http_client=http_client,
        )
        anki_errors.extend(errors)


async def sync_from_feed(
    client: GeoguessrClient,
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    http_client: httpx.AsyncClient,
) -> None:
    print("\n" + translate("🌍 Fetching feed..."))

    entries = await client.fetch_feed_entries()

    if not entries:
        print(translate("⚠️  No games found in feed."))
        return

    print(translate("   {n} game(s) found in feed.\n", n=len(entries)))

    saved_tokens = await db.fetch_saved_challenge_tokens([e["challenge_token"] for e in entries])

    new_entries = [e for e in entries if e["challenge_token"] not in saved_tokens]

    if not new_entries:
        print("\n  " + translate("✅ Up to date, no new games."))
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    anki_errors: list[str] = []

    await asyncio.gather(
        _fetch_worker(client, new_entries, queue),
        _process_worker(
            queue,
            anki_errors,
            db=db,
            geo_client=geo_client,
            anki_client=anki_client,
            http_client=http_client,
        ),
    )

    if anki_errors:
        print("\n  " + translate("⚠️  Anki errors:"))
        for err in anki_errors:
            print(f"      · {err}")
