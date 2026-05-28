"""city.py — City data access layer (asyncpg, $1/$2 placeholders)."""

from dataclasses import dataclass

import asyncpg


@dataclass
class City:
    """Mirrors the city table columns."""

    country_code: str
    name: str
    geometry: str
    state_id: int | None = None
    county: str | None = None
    area_code: str | None = None
    city_id: int = 0


async def upsert_city(pool: asyncpg.Pool, city: City) -> None:
    """Upsert a city on (country_code, state_id, name) conflict."""
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO city (country_code, state_id, county, name, area_code, geometry)
            VALUES ($1, $2, $3, $4, $5, $6)
            ON CONFLICT ON CONSTRAINT uq_city_identity DO UPDATE SET
                area_code = EXCLUDED.area_code,
                geometry  = EXCLUDED.geometry
            """,
            city.country_code,
            city.state_id,
            city.county,
            city.name,
            city.area_code,
            city.geometry,
        )


async def fetch_cities_for_country(pool: asyncpg.Pool, country_code: str) -> list[City]:
    """Return all cities for the given country code."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM city WHERE country_code = $1",
            country_code,
        )
    return [
        City(
            city_id=row["city_id"],
            country_code=row["country_code"],
            state_id=row["state_id"],
            county=row["county"],
            name=row["name"],
            area_code=row["area_code"],
            geometry=row["geometry"],
        )
        for row in rows
    ]
