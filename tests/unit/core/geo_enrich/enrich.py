"""Tests for GeoEnrichClient.enrich and enrich_all."""

from unittest.mock import patch

import httpx
import pytest

from core.geo_enrich import GeoEnrichClient


@pytest.mark.asyncio
async def test_enrich_with_valid_coords_calls_nominatim_and_returns_full_dict() -> None:
    """Enrich with valid coords calls Nominatim via the injected client and returns combined result."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert "nominatim.openstreetmap.org" in str(request.url)
        return httpx.Response(
            200,
            json={
                "address": {
                    "country_code": "ar",
                    "country": "Argentina",
                    "state": "Buenos Aires",
                    "city": "La Plata",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        geo = GeoEnrichClient(http_client=http_client)
        with (
            patch(
                "src.core.eco_enrich.lookup",
                return_value={
                    "realm": "Neotropical",
                    "biome": "Temperate",
                    "ecoregion": "Test Eco",
                },
            ),
            patch("asyncio.sleep"),  # skip Nominatim rate-limit sleep
        ):
            result = await geo.enrich(-34.92, -57.95)

    assert result["lat"] == -34.92
    assert result["lng"] == -57.95
    assert result["country_code"] == "AR"
    assert result["country"] == "Argentina"
    assert result["state"] == "Buenos Aires"
    assert result["city"] == "La Plata"
    assert result["realm"] == "Neotropical"
    assert result["biome"] == "Temperate"
    assert result["ecoregion"] == "Test Eco"


@pytest.mark.asyncio
async def test_enrich_all_gathers_results_for_multiple_coords() -> None:
    """enrich_all calls enrich for each coord and returns a list of results."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "address": {
                    "country_code": "ar",
                    "country": "Argentina",
                    "state": "Buenos Aires",
                    "city": "La Plata",
                },
            },
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        geo = GeoEnrichClient(http_client=http_client)
        with (
            patch(
                "src.core.eco_enrich.lookup",
                return_value={"realm": "X", "biome": "Y", "ecoregion": "Z"},
            ),
            patch("asyncio.sleep"),
        ):
            results = await geo.enrich_all([(-34.92, -57.95), (-34.60, -58.38)])

    assert len(results) == 2
    assert results[0]["lat"] == -34.92
    assert results[1]["lng"] == -58.38
