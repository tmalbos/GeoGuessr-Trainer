from unittest.mock import AsyncMock, MagicMock

import pytest

from db.db import DbAdapter


@pytest.mark.asyncio
async def test_returns_roads_and_plates_when_country_has_signals():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_road = {"road_line_id": 1, "rule": "whole-country", "outer_color": "white"}
    mock_plate = {"license_plate_id": 1, "car_type": "normal", "front_color": "white"}
    mock_conn.fetch.side_effect = [[mock_road], [mock_plate]]
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_country_geo_signals("DE")

    # Assert
    assert len(result["roads"]) == 1
    assert result["roads"][0]["outer_color"] == "white"
    assert len(result["license_plates"]) == 1
    assert result["license_plates"][0]["car_type"] == "normal"


@pytest.mark.asyncio
async def test_returns_empty_lists_when_country_has_no_signals():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_conn.fetch.side_effect = [[], []]
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_country_geo_signals("XX")

    # Assert
    assert result == {"roads": [], "license_plates": []}


@pytest.mark.asyncio
async def test_lowercase_country_code_returns_same_behavior():
    # Arrange
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_ctx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_conn.fetch.side_effect = [[], []]
    adapter = DbAdapter(mock_pool)

    # Act
    result = await adapter.fetch_country_geo_signals("de")

    # Assert — call succeeds and returns expected shape
    assert result == {"roads": [], "license_plates": []}
