from datetime import datetime, timezone
from core.config import MONGO_URI, MONGO_DB, COL_GAMES, COL_ROUNDS

try:
    from pymongo import MongoClient
    _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    _client.server_info()
    _db = _client[MONGO_DB]
    MONGO_OK = True
except Exception:
    _db = None
    MONGO_OK = False


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        import re
        value = re.sub(r'(\.\d{6})\d+', r'\1', value)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


def save_game(game: dict):
    if not MONGO_OK:
        return

    rounds = game.pop("rounds", [])

    game_doc = {
        "game_id":      game["game_id"],
        "map_name":     game["map_name"],
        "played_at":    _parse_datetime(game.get("played_at")),
        "total_score":  game["total_score"],
        "avg_distance": game["avg_distance"],
    }

    _db[COL_GAMES].update_one(
        {"game_id": game_doc["game_id"]},
        {"$set": game_doc},
        upsert=True,
    )

    for r in rounds:
        round_doc = {
            "game_id":      r["game_id"],
            "round_number": r["round_number"],
            "real_geo":     r["real_geo"],
            "guess_geo":    r["guess_geo"],
            "score":        r["score"],
            "distance_km":  r["distance_km"],
            "steps":        r["steps"],
            "time_sec":     r["time_sec"],
        }
        _db[COL_ROUNDS].update_one(
            {"game_id": round_doc["game_id"], "round_number": round_doc["round_number"]},
            {"$set": round_doc},
            upsert=True,
        )

    print(f"\n  💾 Guardado en MongoDB — game_id: {game_doc['game_id']}")