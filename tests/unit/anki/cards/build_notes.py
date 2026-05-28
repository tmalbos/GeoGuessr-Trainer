"""Tests for cards.build_notes with injected AsyncClient."""

from unittest.mock import AsyncMock

import httpx
import pytest

from anki.anki_connect import AnkiConnectClient
from anki.cards import build_notes
from db.db import DbAdapter


@pytest.mark.asyncio
async def test_build_notes_uses_injected_http_client() -> None:
    """build_notes uses the injected AsyncClient for REST Countries calls."""
    request_captured = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_captured
        if "restcountries.com" in str(request.url):
            request_captured = request
        return httpx.Response(
            200,
            json=[
                {
                    "cca2": "AR",
                    "translations": {
                        "eng": {"common": "Argentina"},
                        "spa": {"common": "Argentina"},
                    },
                    "capital": ["Buenos Aires"],
                    "car": {"side": "right"},
                    "tld": [".ar"],
                    "flags": {"png": "https://flagcdn.com/ar.png"},
                },
            ],
        )

    db = AsyncMock(spec=DbAdapter)
    db.fetch_country_geo_signals = AsyncMock(return_value={"roads": [], "license_plates": []})

    anki_client = AsyncMock(spec=AnkiConnectClient)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        notes = await build_notes("AR", db=db, http_client=http_client, anki_client=anki_client)

    assert request_captured is not None
    assert "restcountries.com" in str(request_captured.url)
    assert len(notes) > 0
