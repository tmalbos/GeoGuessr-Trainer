"""
db.py
Capa de acceso a datos — PostgreSQL async con asyncpg.

Dependencias:
    pip install asyncpg python-dotenv
"""

from datetime import datetime

import asyncpg
from asyncpg import Pool

from src.core.config import PG_DSN

# =============================================================
# Pool global
# =============================================================

_pool: Pool | None = None


async def init_pool() -> None:
    """Inicializa el pool de conexiones. Llamar una vez al arrancar la app."""
    global _pool
    _pool = await asyncpg.create_pool(dsn=PG_DSN, min_size=2, max_size=10)


async def close_pool() -> None:
    """Cierra el pool. Llamar al apagar la app."""
    if _pool:
        await _pool.close()


async def check_connection() -> bool:
    """Verifica que Postgres esté disponible."""
    try:
        async with _pool.acquire() as conn:
            await conn.fetchval("SELECT 1")
        return True
    except Exception:
        return False


# =============================================================
# Helpers internos
# =============================================================


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        import re

        value = re.sub(r"(\.\d{6})\d+", r"\1", value)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None


async def _resolve_ecoregion_id(
    conn: asyncpg.Connection,
    country_name: str,
    state_name: str,
    ecoregion_name: str,
) -> int:
    """Resuelve country+state+ecoregion a un ecoregion_id. Lanza si no existe."""
    row = await conn.fetchrow(
        """
        SELECT e.id
        FROM ecoregions e
        JOIN state_ecoregions se ON se.ecoregion_id = e.id
        JOIN states s            ON s.id = se.state_id
        JOIN countries c         ON c.code = s.country_id
        WHERE c.name = $1 AND s.name = $2 AND e.name = $3
        LIMIT 1
        """,
        country_name,
        state_name,
        ecoregion_name,
    )
    if row is None:
        raise ValueError(
            f"ecoregion not found: country={country_name!r} "
            f"state={state_name!r} ecoregion={ecoregion_name!r}"
        )
    return row["id"]


async def _insert_geo_point(conn: asyncpg.Connection, geo: dict) -> int:
    """Inserta un geo_point y devuelve su id."""
    ecoregion_id = await _resolve_ecoregion_id(
        conn,
        geo.get("country", ""),
        geo.get("state", ""),
        geo.get("ecoregion", ""),
    )
    return await conn.fetchval(
        """
        INSERT INTO geo_points (lat, lng, city, ecoregion_id)
        VALUES ($1, $2, $3, $4)
        RETURNING id
        """,
        geo.get("lat"),
        geo.get("lng"),
        geo.get("city"),
        ecoregion_id,
    )


# =============================================================
# Games
# =============================================================


async def save_game(game: dict) -> None:
    rounds = game.pop("rounds", [])

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO games
                    (game_id, challenge_token, is_daily, map_name, played_at, total_score, avg_distance)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                ON CONFLICT (game_id) DO UPDATE SET
                    challenge_token = EXCLUDED.challenge_token,
                    is_daily        = EXCLUDED.is_daily,
                    map_name        = EXCLUDED.map_name,
                    played_at       = EXCLUDED.played_at,
                    total_score     = EXCLUDED.total_score,
                    avg_distance    = EXCLUDED.avg_distance
                """,
                game["game_id"],
                game.get("challenge_token"),
                game.get("is_daily"),
                game["map_name"],
                _parse_datetime(game.get("played_at")),
                game["total_score"],
                game["avg_distance"],
            )

            for r in rounds:
                guess_id = await _insert_geo_point(conn, r["guess_geo"])
                real_id = await _insert_geo_point(conn, r["real_geo"])

                await conn.execute(
                    """
                    INSERT INTO rounds
                        (game_id, round_number, score, distance_km, steps, time_sec,
                         guess_geo_id, real_geo_id)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT DO NOTHING
                    """,
                    r["game_id"],
                    r["round_number"],
                    r.get("score"),
                    r.get("distance_km"),
                    r.get("steps"),
                    r.get("time_sec"),
                    guess_id,
                    real_id,
                )

    print(f"\n  💾 Saved to PostgreSQL — game_id: {game['game_id']}")


# =============================================================
# Queries
# =============================================================


async def fetch_rows(query: str, *args) -> list[dict]:
    """Ejecuta una query y devuelve lista de dicts."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [dict(r) for r in rows]


async def fetch_one(query: str, *args) -> dict | None:
    """Ejecuta una query y devuelve un solo dict o None."""
    async with _pool.acquire() as conn:
        row = await conn.fetchrow(query, *args)
    return dict(row) if row else None
