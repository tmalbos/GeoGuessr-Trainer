"""Tests for AppContext.init — pool creation on initialization."""

from unittest.mock import AsyncMock, patch

import pytest

from core.app_context import AppContext


@pytest.mark.asyncio
async def test_db_pool_is_set_when_context_is_initialized() -> None:
    """init() creates a pool via init_pool() and assigns it to db_pool."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")
    mock_pool = AsyncMock()

    with patch("db.db.asyncpg.create_pool", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_pool

        # Act
        await ctx.init()

    # Assert
    mock_create.assert_called_once_with(dsn=ctx.db_dsn, min_size=2, max_size=10)
    assert ctx.db_pool is mock_pool
    assert ctx._db_adapter is not None
