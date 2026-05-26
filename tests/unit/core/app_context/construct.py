"""Tests for AppContext construction — resource attributes are None at construction."""

from core.app_context import AppContext


def test_resources_are_exposed_as_attributes_when_context_is_created():
    """AppContext exposes db_pool, http_client, geo_client, anki_client as None until init()."""
    # Arrange
    dsn = "postgresql://localhost:5432/geoguessr"

    # Act
    ctx = AppContext(db_dsn=dsn)

    # Assert — all resources are None until init() is called
    assert ctx.db_pool is None
    assert ctx.http_client is None
    assert ctx.geo_client is None
    assert ctx.anki_client is None
    assert ctx.ecoregion_gdf is None
    assert ctx.db_dsn == dsn
