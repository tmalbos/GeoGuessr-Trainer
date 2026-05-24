"""Tests for AnkiConnectClient.add_note."""

import httpx
import pytest

from anki.anki_connect import AnkiConnectClient


@pytest.mark.asyncio
async def test_add_note_invokes_ankiconnect_api():
    """add_note posts the correct payload to the AnkiConnect API."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "addNote" in request.content.decode()
        return httpx.Response(200, json={"result": 1234567890, "error": None})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AnkiConnectClient(http_client=http_client)
        result = await client.add_note(
            deck="GeoGuessr",
            model="Basic",
            fields={"Front": "Test", "Back": "Test"},
            tags=["tag1", "tag2"],
        )

    assert result == 1234567890
