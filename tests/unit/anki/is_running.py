"""Tests for AnkiConnectClient.is_running."""

import httpx
import pytest

from anki.anki_connect import AnkiConnectClient


@pytest.mark.asyncio
async def test_is_running_returns_true_when_server_responds():
    """is_running() returns True when the AnkiConnect server responds."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"result": 6, "error": None})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AnkiConnectClient(http_client=http_client)
        result = await client.is_running()

    assert result is True


@pytest.mark.asyncio
async def test_is_running_returns_false_on_connection_error():
    """is_running() returns False when the server is unreachable."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = AnkiConnectClient(http_client=http_client)
        result = await client.is_running()

    assert result is False
