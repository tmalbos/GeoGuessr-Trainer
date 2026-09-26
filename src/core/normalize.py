"""normalize.py — Turn raw GeoGuessr API payloads into a common round shape."""

from src.core.replay import had_movement, had_pan_or_zoom

REQUIRED_MOVE_FIELDS = ("forbidMoving", "forbidZooming", "forbidRotating")


def move_type_from_challenge(forbid_flags: dict) -> str:
    """Derive move_type for daily/challenge games.

    NOTE: assumes forbidMoving/forbidZooming/forbidRotating live at the top
    level of the /v3/games/{token} response. Confirm against a real payload.
    """
    for key in REQUIRED_MOVE_FIELDS:
        if key not in forbid_flags:
            msg = f"Missing required field '{key}' in game response; refusing to guess move_type."
            raise ValueError(msg)

    if not forbid_flags["forbidMoving"]:
        return "moving"
    if forbid_flags["forbidZooming"] and forbid_flags["forbidRotating"]:
        return "nmpz"
    return "no_move"


def infer_move_type_from_replays(round_replays: list[list[dict]]) -> str:
    """Fallback for duels: infer move_type from replay events, since duels
    don't (currently) expose forbid flags anywhere we've found.
    """
    if any(had_movement(evs) for evs in round_replays):
        return "moving"
    if any(had_pan_or_zoom(evs) for evs in round_replays):
        return "no_move"
    return "nmpz"


def normalize_challenge_rounds(game_data: dict) -> list[dict]:
    rounds_raw = game_data.get("rounds", [])
    guesses = game_data.get("player", {}).get("guesses", [])

    normalized = []
    for i, guess in enumerate(guesses[: len(rounds_raw)], start=1):
        raw = rounds_raw[i - 1]
        dist_m = guess.get("distanceInMeters")
        normalized.append(
            {
                "round_number": i,
                "start_time": raw["startTime"],
                "real_lat": raw["lat"],
                "real_lng": raw["lng"],
                "guess_lat": guess.get("lat"),
                "guess_lng": guess.get("lng"),
                "score": guess.get("roundScoreInPoints", 0),
                "distance_km": round(dist_m / 1000, 1) if dist_m else None,
            },
        )
    return normalized


def normalize_duel_rounds(duel: dict, user_id: str) -> list[dict]:
    normalized = []
    for r in duel.get("rounds", []):
        guess = next((g for g in r.get("guesses", []) if g.get("playerId") == user_id), None)
        dist_m = guess.get("distance") if guess else None
        normalized.append(
            {
                "round_number": r["roundNumber"],
                "start_time": r["startTime"],
                "real_lat": r["correctLat"],
                "real_lng": r["correctLng"],
                "guess_lat": guess.get("lat") if guess else None,
                "guess_lng": guess.get("lng") if guess else None,
                "score": guess.get("score", 0) if guess else 0,
                "distance_km": round(dist_m / 1000, 1) if dist_m else None,
            },
        )
    return normalized
