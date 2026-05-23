from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.db import close_pool, init_pool


@pytest.mark.asyncio
async def test_init_pool_creates_pool_with_dsn():
    mock_pool = AsyncMock()
    with patch("src.db.db.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool
        pool = await init_pool(dsn="postgres://user:pass@localhost/test")

    assert pool is mock_pool
    mock_create.assert_called_once_with(
        dsn="postgres://user:pass@localhost/test", min_size=2, max_size=10
    )


@pytest.mark.asyncio
async def test_init_pool_creates_pool_with_default_dsn():
    mock_pool = AsyncMock()
    with patch("src.db.db.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool
        pool = await init_pool()

    assert pool is mock_pool
    mock_create.assert_called_once_with(dsn="", min_size=2, max_size=10)


@pytest.mark.asyncio
async def test_close_pool_closes_when_not_none():
    mock_pool = MagicMock()
    mock_pool.close = AsyncMock()

    await close_pool(mock_pool)

    mock_pool.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_pool_does_nothing_when_none():
    # Should not raise
    await close_pool(None)
