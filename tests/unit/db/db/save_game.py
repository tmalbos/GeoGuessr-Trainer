from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from db.db import DbAdapter


@pytest.fixture
def mock_db():
    mock_pool = MagicMock()
    mock_conn = MagicMock()
    mock_ctx = AsyncMock()
    mock_tx = AsyncMock()
    mock_pool.acquire.return_value = mock_ctx
    mock_ctx.__aenter__.return_value = mock_conn
    mock_conn.transaction = MagicMock(return_value=AsyncMock())
    mock_conn.transaction.return_value.__aenter__.return_value = mock_tx
    mock_conn.execute = AsyncMock()
    return DbAdapter(mock_pool), mock_conn


@pytest.mark.asyncio
async def test_game_and_rounds_are_saved_with_geo_resolution(mock_db):
    # Arrange
    adapter, mock_conn = mock_db

    game = {
        "challenge_token": "tok1",
        "game_id": "g1",
        "map_name": "World",
        "is_daily": False,
        "played_at": "2024-01-15T12:00:00Z",
        "rounds": [
            {
                "round_number": 1,
                "score": 4500,
                "distance_km": 150.0,
                "steps": 3,
                "time_sec": 45,
                "real_geo": {
                    "country_code": "FR",
                    "state": "",
                    "city": "Paris",
                    "ecoregion": "Atlantic mixed forests",
                    "lat": 48.8566,
                    "lng": 2.3522,
                },
                "guess_geo": {
                    "country_code": "DE",
                    "state": "",
                    "city": "Berlin",
                    "ecoregion": "Central European mixed forests",
                    "lat": 52.52,
                    "lng": 13.405,
                },
            }
        ],
    }

    with (
        patch("db.db._resolve_state_id", return_value=None),
        patch("db.db._resolve_ecoregion", return_value=(1, 10)),
    ):
        # Act
        await adapter.save_game(game)

    # Assert — game was inserted, round was inserted
    assert mock_conn.execute.call_count == 2
    game_insert = mock_conn.execute.call_args_list[0][0][0]
    assert "INSERT INTO game" in game_insert


@pytest.mark.asyncio
async def test_save_game_succeeds_with_empty_rounds(mock_db):
    # Arrange
    adapter, mock_conn = mock_db

    game = {
        "challenge_token": "tok2",
        "game_id": "g2",
        "map_name": "World",
        "is_daily": True,
        "played_at": None,
        "rounds": [],
    }

    # Act
    await adapter.save_game(game)

    # Assert — only game insert, no round inserts
    assert mock_conn.execute.call_count == 1
    assert "INSERT INTO game" in mock_conn.execute.call_args[0][0]
