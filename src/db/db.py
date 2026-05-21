"""
db.py
Capa de acceso a datos — PostgreSQL async con asyncpg.
"""

import os
from datetime import datetime

import asyncpg
from asyncpg import Pool
from dotenv import load_dotenv

load_dotenv()

# =============================================================
# Pool global
# =============================================================

_pool: Pool | None = None


async def init_pool() -> None:
    """Inicializa el pool de conexiones. Llamar una vez al arrancar la app."""
    global _pool
    _pool = await asyncpg.create_pool(dsn=os.environ["PG_DSN"], min_size=2, max_size=10)


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


async def fetch_country_geo_signals(country_code: str) -> dict:
    """Returns road lines and license plates for a given country code."""
    async with _pool.acquire() as conn:
        road_rows = await conn.fetch(
            """
            SELECT rl.*
            FROM road_line rl
            JOIN country_paints_road_line cprl USING (road_line_id)
            WHERE cprl.country_code = $1
            """,
            country_code.upper(),
        )
        plate_rows = await conn.fetch(
            """
            SELECT lp.*
            FROM license_plate lp
            JOIN country_issues_license_plate cilp USING (license_plate_id)
            WHERE cilp.country_code = $1
            """,
            country_code.upper(),
        )
    return {
        "roads": [dict(r) for r in road_rows],
        "license_plates": [dict(r) for r in plate_rows],
    }


async def fetch_all_rounds() -> list[dict]:
    """
    Returns all rounds joined with their game's played_at, enriched with
    geo fields in the shape that stats.py expects:
      real_geo:  {hemisphere, continent, subregion, country, realm, biome, ecoregion}
      guess_geo: {same keys}
    """
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                r.challenge_token,
                r.game_id,
                r.round_number,
                r.score,
                r.distance_km,
                r.steps,
                r.time_sec,
                g.played_at,

                -- real geo
                real_c.name          AS real_country,
                real_s.name          AS real_state,
                r.real_city,
                real_b.realm         AS real_realm,
                real_b.name          AS real_biome,
                real_e.name          AS real_ecoregion,

                -- guess geo (all nullable)
                guess_c.name         AS guess_country,
                guess_s.name         AS guess_state,
                r.guess_city,
                guess_b.realm        AS guess_realm,
                guess_b.name         AS guess_biome,
                guess_e.name         AS guess_ecoregion

            FROM round r
            JOIN game g
                ON  g.challenge_token = r.challenge_token
                AND g.game_id         = r.game_id

            JOIN country  real_c ON real_c.code           = r.real_country_code
            LEFT JOIN state real_s
                ON  real_s.country_code = r.real_country_code
                AND real_s.state_id     = r.real_state_id
            JOIN ecoregion real_e
                ON  real_e.ecoregion_id = r.real_ecoregion_id
                AND real_e.biome_id     = r.real_biome_id
            JOIN biome real_b ON real_b.biome_id = r.real_biome_id

            LEFT JOIN country  guess_c ON guess_c.code           = r.guess_country_code
            LEFT JOIN state    guess_s
                ON  guess_s.country_code = r.guess_country_code
                AND guess_s.state_id     = r.guess_state_id
            LEFT JOIN ecoregion guess_e
                ON  guess_e.ecoregion_id = r.guess_ecoregion_id
                AND guess_e.biome_id     = r.guess_biome_id
            LEFT JOIN biome    guess_b ON guess_b.biome_id = r.guess_biome_id

            ORDER BY g.played_at ASC, r.round_number ASC
            """
        )

    result = []
    for row in rows:
        result.append(
            {
                "challenge_token": row["challenge_token"],
                "game_id": row["game_id"],
                "round_number": row["round_number"],
                "score": row["score"],
                "distance_km": float(row["distance_km"]),
                "steps": row["steps"],
                "time_sec": row["time_sec"],
                "played_at": row["played_at"],
                "real_geo": {
                    "country": row["real_country"],
                    "state": row["real_state"],
                    "city": row["real_city"],
                    "realm": row["real_realm"],
                    "biome": row["real_biome"],
                    "ecoregion": row["real_ecoregion"],
                },
                "guess_geo": {
                    "country": row["guess_country"],
                    "state": row["guess_state"],
                    "city": row["guess_city"],
                    "realm": row["guess_realm"],
                    "biome": row["guess_biome"],
                    "ecoregion": row["guess_ecoregion"],
                },
            }
        )
    return result


async def fetch_saved_challenge_tokens(challenge_tokens: list[str]) -> set[str]:
    """Returns the subset of the given challenge tokens that are already in the DB."""
    async with _pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT DISTINCT challenge_token FROM game WHERE challenge_token = ANY($1)",
            challenge_tokens,
        )
    return {row["challenge_token"] for row in rows}


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


async def _resolve_state_id(
    conn: asyncpg.Connection,
    country_code: str | None,
    state_name: str,
) -> int | None:
    """Returns state_id for (country_code, state name), or None if not found."""
    if not country_code or not state_name:
        return None
    row = await conn.fetchrow(
        "SELECT state_id FROM state WHERE country_code = $1 AND name = $2",
        country_code,
        state_name,
    )
    return row["state_id"] if row else None


async def _resolve_ecoregion(
    conn: asyncpg.Connection,
    ecoregion_name: str,
) -> tuple[int, int] | tuple[None, None]:
    """Returns (biome_id, ecoregion_id) for a given ecoregion name, or (None, None)."""
    if not ecoregion_name:
        return None, None
    row = await conn.fetchrow(
        "SELECT biome_id, ecoregion_id FROM ecoregion WHERE name = $1",
        ecoregion_name,
    )
    if row is None:
        return None, None
    return row["biome_id"], row["ecoregion_id"]


# =============================================================
# Games
# =============================================================


async def save_game(game: dict) -> None:
    rounds = game.pop("rounds", [])

    async with _pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO game
                    (challenge_token, game_id, map_name, is_daily, played_at)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (challenge_token, game_id) DO UPDATE SET
                    map_name  = EXCLUDED.map_name,
                    is_daily  = EXCLUDED.is_daily,
                    played_at = EXCLUDED.played_at
                """,
                game.get("challenge_token"),
                game["game_id"],
                game["map_name"],
                game.get("is_daily"),
                _parse_datetime(game.get("played_at")),
            )

            for r in rounds:
                real_geo = r["real_geo"]
                guess_geo = r["guess_geo"]

                # Resolve country codes
                real_country = real_geo.get("country_code", "")
                guess_country = guess_geo.get("country_code") or None

                # Resolve state IDs (nullable)
                real_state_id = await _resolve_state_id(
                    conn, real_country, real_geo.get("state", "")
                )
                guess_state_id = (
                    await _resolve_state_id(conn, guess_country, guess_geo.get("state", ""))
                    if guess_country
                    else None
                )

                # Resolve ecoregion IDs
                real_biome_id, real_eco_id = await _resolve_ecoregion(
                    conn, real_geo.get("ecoregion", "")
                )
                guess_biome_id, guess_eco_id = (
                    await _resolve_ecoregion(conn, guess_geo.get("ecoregion", ""))
                    if guess_geo.get("ecoregion")
                    else (None, None)
                )

                await conn.execute(
                    """
                    INSERT INTO round (
                        challenge_token, game_id, round_number,
                        guess_latitude, guess_longitude,
                        guess_country_code, guess_state_id, guess_city,
                        guess_biome_id, guess_ecoregion_id,
                        real_latitude,  real_longitude,
                        real_country_code,  real_state_id,  real_city,
                        real_biome_id,  real_ecoregion_id,
                        score, distance_km, steps, time_sec
                    ) VALUES (
                        $1,  $2,  $3,
                        $4,  $5,  $6,  $7,  $8,  $9,  $10,
                        $11, $12, $13, $14, $15, $16, $17,
                        $18, $19, $20, $21
                    )
                    ON CONFLICT DO NOTHING
                    """,
                    game.get("challenge_token"),
                    game["game_id"],
                    r["round_number"],
                    guess_geo.get("lat"),
                    guess_geo.get("lng"),
                    guess_country,
                    guess_state_id,
                    guess_geo.get("city") or None,
                    guess_biome_id,
                    guess_eco_id,
                    real_geo["lat"],
                    real_geo["lng"],
                    real_country,
                    real_state_id,
                    real_geo.get("city") or None,
                    real_biome_id,
                    real_eco_id,
                    r.get("score"),
                    r.get("distance_km"),
                    r.get("steps"),
                    r.get("time_sec"),
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
