"""Tests for GeoEnrichClient construction — resource attributes exist."""

import asyncio

import httpx

from core.geo_enrich import GeoEnrichClient


def test_constructor_stores_client_and_creates_semaphore():
    """GeoEnrichClient stores the injected http client and creates a Semaphore(1) by default."""
    http_client = httpx.AsyncClient()
    geo = GeoEnrichClient(http_client=http_client)

    assert geo._client is http_client
    assert isinstance(geo._semaphore, asyncio.Semaphore)
