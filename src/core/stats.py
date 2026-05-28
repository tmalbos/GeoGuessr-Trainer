"""stats.py — Análisis de performance en GeoGuessr.
DB access and level discovery.
"""

from collections import defaultdict

from src.core.calculator import GEO_LEVELS
from src.db.db import DbAdapter


async def load_rounds(db: DbAdapter) -> list[dict]:
    """Load all rounds from the database via DbAdapter."""
    return await db.fetch_all_rounds()


async def available_levels(db: DbAdapter, min_rounds: int) -> list[tuple]:
    """Discover geo levels with enough rounds for analysis."""
    rounds = await load_rounds(db)
    if not rounds:
        return []
    total = len(rounds)
    result = []
    for level, label in GEO_LEVELS:
        if level == "general":
            if total >= min_rounds:
                result.append((level, label, total))
        else:
            counts: dict[str, int] = defaultdict(int)
            for r in rounds:
                zone = (r.get("real_geo") or {}).get(level, "")
                if zone:
                    counts[zone] += 1
            if any(v >= min_rounds for v in counts.values()):
                result.append((level, label, total))
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
