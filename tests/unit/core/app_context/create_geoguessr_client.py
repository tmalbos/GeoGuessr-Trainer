"""Tests for AppContext.create_geoguessr_client — factory returns configured client."""

from core.app_context import AppContext
from src.core.api import GeoguessrClient


def test_geoguessr_client_is_created_with_configured_cookie():
    """create_geoguessr_client() returns a GeoguessrClient wired to the context cookie."""
    # Arrange
    ctx = AppContext(db_dsn="postgresql://localhost:5432/geoguessr")

    # Act
    client = ctx.create_geoguessr_client()

    # Assert
    assert isinstance(client, GeoguessrClient)
