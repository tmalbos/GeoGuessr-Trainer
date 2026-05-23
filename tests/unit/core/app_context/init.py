"""Tests for AppContext.init — pool creation on initialization."""

from unittest.mock import AsyncMock, patch

import pytest

from core.app_context import AppContext


@pytest.mark.asyncio
async def test_db_pool_is_set_when_context_is_initialized():
    """init() creates a pool and assigns it to db_pool."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr", ncfa_cookie="test")
    mock_pool = AsyncMock()

    with patch("core.app_context.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool
        # Act
        await ctx.init()

    # Assert
    mock_create.assert_called_once_with(dsn=ctx.db_dsn)
    assert ctx.db_pool is mock_pool
