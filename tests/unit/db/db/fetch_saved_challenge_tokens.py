from unittest.mock import AsyncMock, MagicMock

import pytest

from db.db import DbAdapter


@pytest.mark.asyncio
async def test_returns_subset_of_tokens_that_exist_in_db():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    row1 = {"challenge_token": "abc"}
    row2 = {"challenge_token": "def"}
    mock_conn.fetch.return_value = [row1, row2]
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_saved_challenge_tokens(["abc", "def", "ghi"])

    # Assert
    assert result == {"abc", "def"}


@pytest.mark.asyncio
async def test_returns_empty_set_when_no_tokens_exist():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = []
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_saved_challenge_tokens(["xyz"])

    # Assert
    assert result == set()
