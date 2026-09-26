"""sync.py — Pipeline async: fetch → enrich+save → anki. Emits structured events."""

import asyncio
import os

import httpx

from src.anki.anki_connect import AnkiConnectClient
from src.core.analyzer import process_game
from src.core.api import GeoguessrClient
from src.core.events import Emit, Event, noop
from src.core.geo_enrich import GeoEnrichClient
from src.core.normalize import normalize_challenge_rounds, normalize_duel_rounds
from src.db.db import DbAdapter

DEFAULT_USER_ID = "68daf785000ba2a268744f99"
_SENTINEL = None


def _user_id() -> str:
    return os.environ.get("GEOGUESSR_USER_ID") or DEFAULT_USER_ID


async def _log(emit: Emit, message: str, level: str = "info") -> None:
    await emit(Event("log", {"message": message, "level": level}))


# ── Daily / Challenge ────────────────────────────────────────────────────────


async def _fetch_challenge_worker(
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

            match_type = "daily" if entry["is_daily"] else "challenge"
            await queue.put((token, game_token, match_type, game_data))
    finally:
        await queue.put(_SENTINEL)


async def _process_challenge(
    client: GeoguessrClient,
    token: str,
    game_token: str,
    match_type: str,
    game_data: dict,
    db: DbAdapter,
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
        user_id=_user_id(),
        forbid_flags=forbid_flags,
        emit=emit,
    )


# ── Duels ────────────────────────────────────────────────────────────────────


async def _fetch_new_duels(client: GeoguessrClient, db: DbAdapter) -> list[dict]:
    duels = await client.fetch_duel_history()
    game_ids = [d["gameId"] for d in duels]
    # challenge_token == game_id for duels (duplicated on purpose), so this
    # lookup doubles as the "already saved?" check for them too.
    saved = await db.fetch_saved_challenge_tokens(game_ids)
    return [d for d in duels if d["gameId"] not in saved]


async def _process_duel(
    client: GeoguessrClient,
    duel: dict,
    db: DbAdapter,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    emit: Emit,
) -> list[str]:
    game_id = duel["gameId"]
    normalized = normalize_duel_rounds(duel, _user_id())
    played_at = duel["rounds"][0].get("startTime") if duel.get("rounds") else None

    return await process_game(
        normalized,
        game_id=game_id,
        challenge_token=game_id,
        match_type="duel",
        map_name="World",
        played_at=played_at,
        time_limit_sec=None,
        db=db,
        geo_client=geo_client,
        anki_client=anki_client,
        client=client,
        user_id=_user_id(),
        emit=emit,
    )


# ── Orchestration ────────────────────────────────────────────────────────────


async def sync_from_feed(
    client: GeoguessrClient,
    db: DbAdapter,
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
        await _log(emit, "Fetching feed...")
        entries = await client.fetch_feed_entries()
        entries = [e for e in entries if ("daily" if e["is_daily"] else "challenge") in match_types]
        saved = await db.fetch_saved_challenge_tokens([e["challenge_token"] for e in entries])
        new_entries = [e for e in entries if e["challenge_token"] not in saved]
        await emit(Event("feed", {"found": len(entries), "new": len(new_entries)}))

        if new_entries:
            queue: asyncio.Queue = asyncio.Queue(maxsize=4)

            async def _drain() -> None:
                while (item := await queue.get()) is not _SENTINEL:
                    token, game_token, match_type, game_data = item
                    anki_errors.extend(
                        await _process_challenge(
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
                _fetch_challenge_worker(client, new_entries, queue, emit),
                _drain(),
            )
        else:
            await _log(emit, "No new daily/challenge games.", "success")

    if "duel" in match_types:
        await _log(emit, "Fetching duel history...")
        new_duels = await _fetch_new_duels(client, db)
        await emit(Event("feed", {"found": len(new_duels), "new": len(new_duels)}))
        for duel in new_duels:
            anki_errors.extend(
                await _process_duel(client, duel, db, geo_client, anki_client, emit),
            )

    if anki_errors:
        await emit(Event("anki_errors", {"errors": anki_errors}))
