"""db.py — Capa de acceso a datos PostgreSQL async con asyncpg."""

import json
from datetime import datetime

import asyncpg
from asyncpg import Pool

_HISTORY_SORT_COLUMNS = {
    "date": "g.played_at",
    "total_score": "total_score",
    "total_steps": "total_steps",
    "total_time": "total_time_sec",
}


async def init_pool(dsn: str = "") -> Pool:
    """Create and return a connection pool. Call once at startup."""
    return await asyncpg.create_pool(dsn=dsn, min_size=2, max_size=10)


async def close_pool(pool: Pool | None) -> None:
    """Close the given pool. Call at shutdown."""
    if pool:
        await pool.close()


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        import re

        value = re.sub(r"(\.\d{6})\d+", r"\1", value)
        return datetime.fromisoformat(value)
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


# ── DbAdapter ─────────────────────────────────────────────────


class DbAdapter:
    """Wraps an asyncpg pool and exposes all DB operations as methods."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def check_connection(self) -> bool:
        """Return True if the pool is reachable, False otherwise."""
        try:
            async with self._pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception:
            return False

    async def fetch_country_geo_signals(self, country_code: str) -> dict:
        """Return road lines and license plates for a given country code."""
        async with self._pool.acquire() as conn:
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

    async def fetch_all_rounds(
        self,
        match_type: str,
        move_type: str,
        time_limit_sec: int | None,
    ) -> list[dict]:
        """Return all rounds for one exact (match_type, move_type, time_limit_sec) combo."""
        async with self._pool.acquire() as conn:
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

                    real_c.continent     AS real_continent,
                    real_c.name          AS real_country,
                    real_s.name          AS real_state,
                    r.real_city,
                    real_b.realm         AS real_realm,
                    real_b.name          AS real_biome,
                    real_e.name          AS real_ecoregion,

                    guess_c.continent    AS guess_continent,
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

                WHERE g.match_type = $1
                  AND g.move_type  = $2
                  AND g.time_limit_sec IS NOT DISTINCT FROM $3

                ORDER BY g.played_at ASC, r.round_number ASC
                """,
                match_type,
                move_type,
                time_limit_sec,
            )

        return [
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
                    "continent": row["real_continent"],
                    "country": row["real_country"],
                    "state": row["real_state"],
                    "city": row["real_city"],
                    "realm": row["real_realm"],
                    "biome": row["real_biome"],
                    "ecoregion": row["real_ecoregion"],
                },
                "guess_geo": {
                    "continent": row["guess_continent"],
                    "country": row["guess_country"],
                    "state": row["guess_state"],
                    "city": row["guess_city"],
                    "realm": row["guess_realm"],
                    "biome": row["guess_biome"],
                    "ecoregion": row["guess_ecoregion"],
                },
            }
            for row in rows
        ]

    async def fetch_analysis_filter_options(self) -> list[dict]:
        """Distinct (match_type, move_type, time_limit_sec) combos that have ≥1 round."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT DISTINCT g.match_type, g.move_type, g.time_limit_sec
                FROM game g
                JOIN round r
                    ON r.challenge_token = g.challenge_token AND r.game_id = g.game_id
                ORDER BY g.match_type, g.move_type, g.time_limit_sec NULLS LAST
                """,
            )
        return [dict(r) for r in rows]

    async def fetch_game_history(
        self,
        match_type: str | None,
        move_type: str | None,
        time_limit_mode: str,  # "any" | "null" | "value"
        time_limit_value: int | None,
        min_score: int | None,
        max_score: int | None,
        sort_by: str,
        sort_dir: str,
    ) -> list[dict]:
        """One row per game, with its rounds (ordered) and totals. Never mixes
        match_type/move_type/time_limit — caller passes an exact combo or 'any'.
        """
        if sort_by not in _HISTORY_SORT_COLUMNS:
            msg = f"Invalid sort_by: {sort_by}"
            raise ValueError(msg)
        if sort_dir not in {"asc", "desc"}:
            msg = f"Invalid sort_dir: {sort_dir}"
            raise ValueError(msg)

        order_expr = f"{_HISTORY_SORT_COLUMNS[sort_by]} {sort_dir.upper()}"

        query = f"""
            SELECT
                g.challenge_token,
                g.game_id,
                g.match_type::text AS match_type,
                g.move_type::text  AS move_type,
                g.time_limit_sec,
                g.played_at,
                SUM(r.score)    AS total_score,
                SUM(r.steps)    AS total_steps,
                SUM(r.time_sec) AS total_time_sec,
                json_agg(
                    json_build_object(
                        'round_number', r.round_number,
                        'score', r.score,
                        'steps', r.steps,
                        'time_sec', r.time_sec,
                        'country_code', r.real_country_code
                    ) ORDER BY r.round_number
                ) AS rounds
            FROM game g
            JOIN round r ON r.challenge_token = g.challenge_token AND r.game_id = g.game_id
            WHERE ($1::text IS NULL OR g.match_type::text = $1)
              AND ($2::text IS NULL OR g.move_type::text = $2)
              AND (
                  $3 = 'any'
                  OR ($3 = 'null'  AND g.time_limit_sec IS NULL)
                  OR ($3 = 'value' AND g.time_limit_sec = $4)
              )
            GROUP BY g.challenge_token, g.game_id, g.match_type, g.move_type, g.time_limit_sec, g.played_at
            HAVING ($5::int IS NULL OR SUM(r.score) >= $5)
               AND ($6::int IS NULL OR SUM(r.score) <= $6)
            ORDER BY {order_expr}
        """

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                query,
                match_type,
                move_type,
                time_limit_mode,
                time_limit_value,
                min_score,
                max_score,
            )

        result = []
        for row in rows:
            d = dict(row)
            d["rounds"] = json.loads(d["rounds"]) if isinstance(d["rounds"], str) else d["rounds"]
            result.append(d)
        return result

    async def fetch_saved_challenge_tokens(self, challenge_tokens: list[str]) -> set[str]:
        """Return the subset of the given challenge tokens that are already in the DB."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT DISTINCT challenge_token FROM game WHERE challenge_token = ANY($1)",
                challenge_tokens,
            )
        return {row["challenge_token"] for row in rows}

    async def save_game(self, game: dict) -> None:
        """Persist a game, its rounds, and round replays in a single transaction."""
        rounds = game.get("rounds", [])

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO game
                    (challenge_token, game_id, map_name, match_type, round_count,
                     time_limit_sec, move_type, played_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                ON CONFLICT (challenge_token, game_id) DO UPDATE SET
                    map_name       = EXCLUDED.map_name,
                    match_type     = EXCLUDED.match_type,
                    round_count    = EXCLUDED.round_count,
                    time_limit_sec = EXCLUDED.time_limit_sec,
                    move_type      = EXCLUDED.move_type,
                    played_at      = EXCLUDED.played_at
                """,
                game["challenge_token"],
                game["game_id"],
                game["map_name"],
                game["match_type"],
                game["round_count"],
                game.get("time_limit_sec"),
                game["move_type"],
                _parse_datetime(game.get("played_at")),
            )

            for r in rounds:
                real_geo = r["real_geo"]
                guess_geo = r["guess_geo"]

                real_country = real_geo.get("country_code", "")
                guess_country = guess_geo.get("country_code") or None

                real_state_id = await _resolve_state_id(
                    conn, real_country, real_geo.get("state", "")
                )
                guess_state_id = (
                    await _resolve_state_id(conn, guess_country, guess_geo.get("state", ""))
                    if guess_country
                    else None
                )

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
                    game["challenge_token"],
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
                    r["steps"],
                    r["time_sec"],
                )

                replay_events = r.get("replay")
                if replay_events is not None:
                    await conn.execute(
                        """
                        INSERT INTO round_replay (challenge_token, game_id, round_number, events)
                        VALUES ($1, $2, $3, $4::jsonb)
                        ON CONFLICT (challenge_token, game_id, round_number) DO UPDATE SET
                            events = EXCLUDED.events
                        """,
                        game["challenge_token"],
                        game["game_id"],
                        r["round_number"],
                        json.dumps(replay_events),
                    )

        print(f"\n  💾 Saved to PostgreSQL — game_id: {game['game_id']}")

    async def fetch_rows(self, query: str, *args) -> list[dict]:
        """Execute a query and return rows as a list of dicts."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
        return [dict(r) for r in rows]

    async def fetch_one(self, query: str, *args) -> dict | None:
        """Execute a query and return a single dict or None."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *args)
        return dict(row) if row else None
