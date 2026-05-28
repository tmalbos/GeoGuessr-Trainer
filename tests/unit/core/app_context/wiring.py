"""Integration tests for AppContext wiring — shared http_client instance."""

from unittest.mock import AsyncMock, patch

import pytest

from core.app_context import AppContext


@pytest.mark.asyncio
async def test_http_client_is_closed_on_aclose() -> None:
    """aclose() triggers http_client.aclose()."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    ctx.http_client = AsyncMock()

    # Act
    await ctx.aclose()

    # Assert
    ctx.http_client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_wiring_with_init_does_not_break() -> None:
    """After await ctx.init(), the shared http_client wiring is still intact."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")

    with (
        patch("core.app_context.init_pool", new_callable=AsyncMock, return_value=AsyncMock()),
        patch("core.app_context.close_pool", new_callable=AsyncMock),
    ):
        await ctx.init()
        await ctx.anki_ready.wait()
        await ctx.geo_ready.wait()

        # Assert — both clients share the same http_client instance
        assert ctx.geo_client._client is ctx.http_client
        assert ctx.anki_client._client is ctx.http_client
