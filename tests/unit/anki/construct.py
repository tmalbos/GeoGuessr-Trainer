"""Tests for AnkiConnectClient construction."""

import httpx

from anki.anki_connect import AnkiConnectClient


def test_constructor_stores_client():
    """AnkiConnectClient stores the injected http client."""
    http_client = httpx.AsyncClient()
    client = AnkiConnectClient(http_client=http_client)
    assert client._client is http_client
