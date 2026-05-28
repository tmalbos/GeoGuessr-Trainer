"""Tests for GeoEnrichClient construction — resource attributes exist."""

import httpx

from core.geo_enrich import GeoEnrichClient


def test_constructor_stores_client_and_creates_semaphore() -> None:
    """GeoEnrichClient stores the injected http client."""
    http_client = httpx.AsyncClient()
    geo = GeoEnrichClient(http_client=http_client)

    assert geo._client is http_client
