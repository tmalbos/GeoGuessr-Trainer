"""Tests for AppContext construction — resource attributes exist."""

import httpx

from core.app_context import AnkiConnectClient, AppContext, GeoEnrichClient


def test_resources_are_exposed_as_attributes_when_context_is_created():
    """AppContext exposes db_pool, http_client, geo_client, anki_client, cookie."""
    # Arrange
    dsn = "postgresql://localhost:5432/geoguessr"
    cookie = "test-ncfa-value"

    # Act
    ctx = AppContext(db_dsn=dsn, ncfa_cookie=cookie)

    # Assert
    assert ctx.db_pool is None
    assert isinstance(ctx.http_client, httpx.AsyncClient)
    assert isinstance(ctx.geo_client, GeoEnrichClient)
    assert isinstance(ctx.anki_client, AnkiConnectClient)
    assert ctx.cookie == cookie
    assert ctx.ecoregion_gdf is None
    assert ctx.db_dsn == dsn
