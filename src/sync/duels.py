"""duels.py — Duel games: find unsaved duels and process one."""

from src.anki.anki_connect import AnkiConnectClient
from src.db.repositories.game_repository import GameRepository
from src.geo.enrichment_service import GeoEnrichClient
from src.geoguessr.client import GeoguessrClient
from src.geoguessr.normalize import normalize_duel_rounds
from src.shared.events import Emit
from src.sync.game_ingest import process_game
from src.sync.user_identity import user_id


async def fetch_new_duels(client: GeoguessrClient, db: GameRepository) -> list[dict]:
    duels = await client.fetch_duel_history()
    game_ids = [d["gameId"] for d in duels]
    # challenge_token == game_id for duels (duplicated on purpose), so this
    # lookup doubles as the "already saved?" check for them too.
    saved = await db.fetch_saved_challenge_tokens(game_ids)
    return [d for d in duels if d["gameId"] not in saved]


async def process_duel(
    client: GeoguessrClient,
    duel: dict,
    db: GameRepository,
    geo_client: GeoEnrichClient,
    anki_client: AnkiConnectClient,
    emit: Emit,
) -> list[str]:
    game_id = duel["gameId"]
    normalized = normalize_duel_rounds(duel, user_id())
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
        user_id=user_id(),
        emit=emit,
    )
