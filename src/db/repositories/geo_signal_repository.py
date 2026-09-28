"""geo_signal_repository.py — Road lines and license plates per country (feeds Anki cards)."""

import asyncpg


class GeoSignalRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

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
