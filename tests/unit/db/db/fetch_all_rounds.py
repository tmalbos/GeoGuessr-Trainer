from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from db.db import DbAdapter


def _make_row(**overrides) -> dict:
    defaults = {
        "challenge_token": "tok1",
        "game_id": "g1",
        "round_number": 1,
        "score": 4500,
        "distance_km": 150.0,
        "steps": 3,
        "time_sec": 45,
        "played_at": datetime(2024, 1, 15, 12, 0, 0),
        "real_country": "France",
        "real_state": None,
        "real_city": "Paris",
        "real_realm": "Palearctic",
        "real_biome": "Temperate Broadleaf",
        "real_ecoregion": "Atlantic mixed forests",
        "guess_country": "Germany",
        "guess_state": None,
        "guess_city": "Berlin",
        "guess_realm": "Palearctic",
        "guess_biome": "Temperate Broadleaf",
        "guess_ecoregion": "Central European mixed forests",
    }
    defaults.update(overrides)
    return defaults


@pytest.mark.asyncio
async def test_returns_all_rounds_with_geo_fields():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    row1 = _make_row()
    row2 = _make_row(
        challenge_token="tok1",
        game_id="g1",
        round_number=2,
        score=4200,
        distance_km=300.0,
    )
    mock_conn.fetch.return_value = [row1, row2]
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_all_rounds()

    # Assert
    assert len(result) == 2
    assert result[0]["challenge_token"] == "tok1"
    assert result[0]["real_geo"]["country"] == "France"
    assert result[0]["guess_geo"]["country"] == "Germany"
    assert result[0]["real_geo"]["realm"] == "Palearctic"
    assert result[0]["guess_geo"]["ecoregion"] == "Central European mixed forests"

    assert result[1]["round_number"] == 2
    assert result[1]["distance_km"] == 300.0


@pytest.mark.asyncio
async def test_returns_empty_list_when_no_rounds():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_conn.fetch.return_value = []
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_all_rounds()

    # Assert
    assert result == []
