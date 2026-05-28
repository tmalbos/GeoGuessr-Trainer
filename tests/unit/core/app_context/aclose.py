"""Tests for AppContext.aclose — resource cleanup."""

from unittest.mock import AsyncMock, patch

import pytest

from core.app_context import AppContext


@pytest.mark.asyncio
async def test_resources_are_released_when_context_is_closed() -> None:
    """aclose() closes the pool and http client, clears resource attributes."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    mock_pool = AsyncMock()

    with (
        patch("core.app_context.init_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("core.app_context.close_pool", new_callable=AsyncMock) as mock_close_pool,
        patch("core.app_context.load_cookie", return_value="fake-cookie"),
    ):
        await ctx.init()

        # Act
        await ctx.aclose()

    # Assert
    mock_close_pool.assert_awaited_once_with(mock_pool)
    assert ctx.db_pool is None
    assert ctx.ecoregion_gdf is None


@pytest.mark.asyncio
async def test_aclose_is_idempotent_when_called_twice() -> None:
    """Calling aclose() twice does not raise."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    mock_pool = AsyncMock()

    with (
        patch("core.app_context.init_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch("core.app_context.close_pool", new_callable=AsyncMock),
        patch("core.app_context.load_cookie", return_value="fake-cookie"),
    ):
        await ctx.init()

        # Act & Assert
        await ctx.aclose()
        await ctx.aclose()  # second call — must not raise
