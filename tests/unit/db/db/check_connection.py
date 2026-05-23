from unittest.mock import AsyncMock, MagicMock

import pytest

from db.db import DbAdapter


@pytest.mark.asyncio
async def test_connection_reported_alive_when_pool_responds():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_conn.fetchval.return_value = 1
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.check_connection()

    # Assert
    assert result is True


@pytest.mark.asyncio
async def test_connection_reported_dead_when_pool_raises():
    # Arrange
    mock_pool = MagicMock()
    mock_ctx = AsyncMock()
    mock_ctx.__aenter__.side_effect = Exception("connection refused")
    mock_pool.acquire.return_value = mock_ctx
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.check_connection()

    # Assert
    assert result is False
