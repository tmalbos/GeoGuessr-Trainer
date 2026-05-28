from unittest.mock import AsyncMock, MagicMock

import pytest

from db.models.city import City, upsert_city


def _make_pool() -> tuple[MagicMock, MagicMock]:
    pool = MagicMock()
    conn = MagicMock()
    ctx = AsyncMock()
    pool.acquire.return_value = ctx
    ctx.__aenter__.return_value = conn
    conn.execute = AsyncMock()
    return pool, conn


_PARIS = City(
    country_code="FR",
    name="Paris",
    geometry='{"type":"Polygon","coordinates":[]}',
)


@pytest.mark.asyncio
async def test_upsert_city_executes_insert_into_city() -> None:
    pool, conn = _make_pool()

    await upsert_city(pool, _PARIS)

    conn.execute.assert_called_once()
    sql = conn.execute.call_args[0][0]
    assert "INSERT INTO city" in sql


@pytest.mark.asyncio
async def test_upsert_city_is_idempotent() -> None:
    pool, conn = _make_pool()

    await upsert_city(pool, _PARIS)
    await upsert_city(pool, _PARIS)

    assert conn.execute.call_count == 2
