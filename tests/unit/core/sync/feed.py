"""Tests for sync.sync_from_feed with injected GeoguessrClient."""

from unittest.mock import AsyncMock

import httpx
import pytest

from anki.anki_connect import AnkiConnectClient
from core.api import GeoguessrClient
from core.geo_enrich import GeoEnrichClient
from core.sync import sync_from_feed
from db.db import DbAdapter


@pytest.mark.asyncio
async def test_sync_from_feed_uses_injected_geoguessr_client():
    """sync_from_feed uses the injected GeoguessrClient, does not create one internally."""
    gg_client = AsyncMock(spec=GeoguessrClient)
    gg_client.fetch_feed_entries = AsyncMock(return_value=[])
    db = AsyncMock(spec=DbAdapter)

    async with httpx.AsyncClient() as http_client:
        geo_client = GeoEnrichClient(http_client=http_client)
        anki_client = AnkiConnectClient(http_client=http_client)

        await sync_from_feed(
            client=gg_client,
            db=db,
            geo_client=geo_client,
            anki_client=anki_client,
            http_client=http_client,
        )

    gg_client.fetch_feed_entries.assert_awaited_once()
