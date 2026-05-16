"""
sync.py — Pipeline async: fetch → enrich+save → anki
"""

import asyncio

from src.core.analyzer import process_game
from src.core.api import GeoguessrClient
from src.core.config import COL_GAMES
from src.db.mongo import find_documents

USER_ID = "68daf785000ba2a268744f99"

_SENTINEL = None


async def _fetch_worker(client: GeoguessrClient, entries: list[dict], queue: asyncio.Queue):
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
            print(f"  ⚠️  [{challenge_token}] Sin game token.")
            continue

        try:
            game_data = await client.fetch_game(game_token)
        except ValueError as e:
            print(f"  ❌ [{game_token}] No encontrado: {e}")
            continue
        except Exception as e:
            print(f"  ❌ [{game_token}] Error inesperado: {e}")
            continue

        game_data["challenge_token"] = challenge_token
        game_data["is_daily"] = is_daily
        await queue.put((game_token, game_data))

    await queue.put(_SENTINEL)


async def _process_worker(queue: asyncio.Queue, anki_errors: list[str]):
    """Consume la queue, enriquece, guarda y genera cards."""
    while True:
        item = await queue.get()
        if item is _SENTINEL:
            break
        game_token, game_data = item
        print(f"  🔍 [{game_token}] Procesando...")
        errors = await process_game(game_data, game_token)
        anki_errors.extend(errors)


async def sync_from_feed(ncfa_cookie: str) -> None:
    client = GeoguessrClient(ncfa_cookie)

    print("\n🌍 Obteniendo feed...")
    entries = await client.fetch_feed_entries()
    if not entries:
        print("⚠️  No se encontraron partidas en el feed.")
        await client.aclose()
        return

    print(f"   {len(entries)} partida(s) encontradas en el feed.\n")

    saved = await find_documents(
        COL_GAMES,
        {"challenge_token": {"$in": [e["challenge_token"] for e in entries]}},
        {"challenge_token": 1, "_id": 0},
    )
    saved_tokens = {doc["challenge_token"] for doc in saved}

    new_entries = []
    for entry in entries:
        if entry["challenge_token"] in saved_tokens:
            print(f"  🔁 [{entry['challenge_token']}] Ya existe en la base de datos.")
        else:
            new_entries.append(entry)

    if not new_entries:
        print("\n  ✅ Todo al día, no hay partidas nuevas.")
        await client.aclose()
        return

    queue: asyncio.Queue = asyncio.Queue(maxsize=4)
    anki_errors: list[str] = []

    await asyncio.gather(
        _fetch_worker(client, new_entries, queue),
        _process_worker(queue, anki_errors),
    )

    await client.aclose()

    if anki_errors:
        print("\n  ⚠️  Errores de Anki:")
        for err in anki_errors:
            print(f"      · {err}")
