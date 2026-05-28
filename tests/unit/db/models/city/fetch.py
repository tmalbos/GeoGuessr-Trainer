from unittest.mock import AsyncMock, MagicMock

import pytest

from db.models.city import City, fetch_cities_for_country


def _make_pool(rows: list[dict]) -> MagicMock:
    pool = MagicMock()
    conn = MagicMock()
    ctx = AsyncMock()
    pool.acquire.return_value = ctx
    ctx.__aenter__.return_value = conn
    conn.fetch = AsyncMock(return_value=rows)
    return pool


_ROW: dict = {
    "city_id": 1,
    "country_code": "FR",
    "state_id": None,
    "county": None,
    "name": "Paris",
    "area_code": "75",
    "geometry": '{"type":"Polygon","coordinates":[]}',
}


@pytest.mark.asyncio
async def test_fetch_cities_for_country_returns_city_list() -> None:
    pool = _make_pool([_ROW])

    result = await fetch_cities_for_country(pool, "FR")

    assert result == [
        City(
            city_id=1,
            country_code="FR",
            name="Paris",
            geometry='{"type":"Polygon","coordinates":[]}',
            area_code="75",
        ),
    ]


@pytest.mark.asyncio
async def test_fetch_cities_for_country_returns_empty_list_when_no_rows() -> None:
    pool = _make_pool([])

    result = await fetch_cities_for_country(pool, "XX")

    assert result == []
