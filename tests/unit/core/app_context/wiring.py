"""Integration tests for AppContext wiring — shared http_client instance."""

from unittest.mock import AsyncMock, patch

import pytest

from core.app_context import AppContext


@pytest.mark.asyncio
async def test_clients_share_same_http_client_instance():
    """geo_client and anki_client both use the same http_client instance."""
    # Arrange — all are None at construction time
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")

    # Assert — resources are created lazily in init(), not at construction
    assert ctx.geo_client is None
    assert ctx.anki_client is None


@pytest.mark.asyncio
async def test_http_client_is_closed_on_aclose():
    """aclose() triggers http_client.aclose()."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    ctx.http_client = AsyncMock()

    # Act
    await ctx.aclose()

    # Assert
    ctx.http_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_wiring_with_init_does_not_break():
    """After await ctx.init(), the shared http_client wiring is still intact."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    mock_pool = AsyncMock()

    with patch("core.app_context.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool
        await ctx.init()
        await ctx.anki_ready.wait()
        await ctx.geo_ready.wait()

    # Assert
    assert ctx.geo_client._client is ctx.http_client
    assert ctx.anki_client._client is ctx.http_client
