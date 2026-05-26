"""Tests for AppContext.aclose — resource cleanup."""

from unittest.mock import AsyncMock, patch

import pytest

from core.app_context import AppContext


@pytest.mark.asyncio
async def test_resources_are_released_when_context_is_closed():
    """aclose() closes the pool and http client, clears resource attributes."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    mock_pool = AsyncMock()

    with patch("core.app_context.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool
        await ctx.init()

    # Act
    await ctx.aclose()

    # Assert
    mock_pool.close.assert_awaited_once()
    assert ctx.db_pool is None
    assert ctx.ecoregion_gdf is None


@pytest.mark.asyncio
async def test_aclose_is_idempotent_when_called_twice():
    """Calling aclose() twice does not raise."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    mock_pool = AsyncMock()

    with patch("core.app_context.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool
        await ctx.init()

    # Act & Assert
    await ctx.aclose()
    await ctx.aclose()  # second call — must not raise
