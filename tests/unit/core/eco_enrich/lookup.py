import pytest

from core.eco_enrich import lookup

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_gdf(realm: str = "Palearctic", biome: str = "Temperate Broadleaf", eco: str = "Test Eco"):
    """Minimal GeoDataFrame-like stub that makes sindex.query / sindex.nearest work."""
    from shapely.geometry import box

    try:
        import geopandas as gpd
    except ImportError:
        pytest.skip("geopandas not installed")

    gdf = gpd.GeoDataFrame(
        {
            "REALM": [realm],
            "BIOME_NAME": [biome],
            "ECO_NAME": [eco],
            "geometry": [box(-180, -90, 180, 90)],  # cubre todo el mundo
        },
        crs="EPSG:4326",
    )
    return gdf


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_returns_all_fields_when_point_is_inside_a_polygon():
    # Arrange
    gdf = _make_gdf(realm="Neotropical", biome="Tropical Moist Broadleaf", eco="Amazon")

    # Act
    result = lookup(-3.0, -60.0, gdf)  # Amazonas, Brasil

    # Assert
    assert result["realm"] == "Neotropical"
    assert result["biome"] == "Tropical Moist Broadleaf"
    assert result["ecoregion"] == "Amazon"


def test_returns_nearest_polygon_when_point_falls_outside_all_polygons():
    # Arrange — polígono que NO contiene el punto (costa)
    try:
        import geopandas as gpd
    except ImportError:
        pytest.skip("geopandas not installed")

    from shapely.geometry import box

    gdf = gpd.GeoDataFrame(
        {
            "REALM": ["Palearctic"],
            "BIOME_NAME": ["Temperate Broadleaf"],
            "ECO_NAME": ["Coastal Eco"],
            "geometry": [box(9.0, 55.0, 11.0, 57.0)],  # Dinamarca, no cubre la costa exacta
        },
        crs="EPSG:4326",
    )

    # Act — Punto costero fuera del polígono pero cercano
    result = lookup(55.85, 10.10, gdf)

    # Assert — debe devolver el polígono más cercano, nunca vacío
    assert result["realm"] != ""
    assert result["biome"] != ""
    assert result["ecoregion"] != ""


def test_raises_when_latitude_is_none():
    # Arrange
    gdf = _make_gdf()

    # Act & Assert
    with pytest.raises(ValueError):
        lookup(None, 10.0, gdf)


def test_raises_when_longitude_is_none():
    # Arrange
    gdf = _make_gdf()

    # Act & Assert
    with pytest.raises(ValueError):
        lookup(55.0, None, gdf)


def test_raises_when_gdf_is_none():
    # Act & Assert
    with pytest.raises(Exception):
        lookup(55.0, 10.0, None)


def test_result_never_contains_empty_strings_for_valid_coordinates():
    # Arrange
    gdf = _make_gdf(realm="Afrotropic", biome="Tropical Grasslands", eco="Serengeti")

    # Act
    result = lookup(-2.3, 34.8, gdf)  # Tanzania

    # Assert — invariante: ningún campo vacío para coordenadas válidas
    assert all(v != "" for v in result.values()), (
        f"lookup devolvió campos vacíos para coordenadas válidas: {result}"
    )
    assert set(result.keys()) == {"realm", "biome", "ecoregion"}
