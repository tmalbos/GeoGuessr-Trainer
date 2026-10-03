"""report.py — DB access and level discovery for analysis."""

from collections import defaultdict

from src.analysis.geo_levels import GEO_LEVELS
from src.db.repositories.game_repository import GameRepository


async def load_rounds(
    db: GameRepository,
    match_type: str,
    move_type: str,
    time_limit_sec: int | None,
) -> list[dict]:
    """Load rounds for one exact (match_type, move_type, time_limit) combo."""
    return await db.fetch_all_rounds(match_type, move_type, time_limit_sec)


async def available_levels(
    db: GameRepository,
    min_rounds: int,
    match_type: str,
    move_type: str,
    time_limit_sec: int | None,
) -> list[tuple]:
    """Geo levels that have at least one zone with enough rounds, as (level, label, zone_count)."""
    rounds = await load_rounds(db, match_type, move_type, time_limit_sec)
    if not rounds:
        return []
    total = len(rounds)
    result = []
    for level, label in GEO_LEVELS:
        if level == "general":
            if total >= min_rounds:
                result.append((level, label, 1))
        else:
            counts: dict[str, int] = defaultdict(int)
            for r in rounds:
                zone = (r.get("real_geo") or {}).get(level, "")
                if zone:
                    counts[zone] += 1
            zones = sum(1 for v in counts.values() if v >= min_rounds)
            if zones:
                result.append((level, label, zones))
    return result


def build_groups(rounds: list[dict], geo_level: str | None) -> dict[str, list[dict]]:
    """Group rounds by geo level key for printer consumption."""
    groups: dict[str, list] = defaultdict(list)
    for r in rounds:
        key = (
            "_global_"
            if geo_level is None
            else ((r.get("real_geo") or {}).get(geo_level, "") or "")
        )
        if key:
            groups[key].append(r)
    return groups
