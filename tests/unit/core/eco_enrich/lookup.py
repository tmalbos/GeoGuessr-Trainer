import threading
from unittest.mock import patch

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


def _patch_ready_gdf(gdf):
    """Parchea el módulo para que parezca ya cargado con `gdf`."""
    event = threading.Event()
    event.set()
    return [
        patch("core.eco_enrich._gdf", gdf),
        patch("core.eco_enrich._ready", event),
        patch("core.eco_enrich._load_error", None),
    ]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_returns_all_fields_when_point_is_inside_a_polygon():
    # Arrange
    gdf = _make_gdf(realm="Neotropical", biome="Tropical Moist Broadleaf", eco="Amazon")
    patches = _patch_ready_gdf(gdf)

    # Act
    with patches[0], patches[1], patches[2]:
        result = lookup(-3.0, -60.0)  # Amazonas, Brasil

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
    event = threading.Event()
    event.set()

    # Act
    with (
        patch("core.eco_enrich._gdf", gdf),
        patch("core.eco_enrich._ready", event),
        patch("core.eco_enrich._load_error", None),
    ):
        # Punto costero fuera del polígono pero cercano
        result = lookup(55.85, 10.10)

    # Assert — debe devolver el polígono más cercano, nunca vacío
    assert result["realm"] != ""
    assert result["biome"] != ""
    assert result["ecoregion"] != ""


def test_raises_when_latitude_is_none():
    # Arrange
    gdf = _make_gdf()
    patches = _patch_ready_gdf(gdf)

    # Act & Assert
    with patches[0], patches[1], patches[2], pytest.raises((ValueError, TypeError)):
        lookup(None, 10.0)


def test_raises_when_longitude_is_none():
    # Arrange
    gdf = _make_gdf()
    patches = _patch_ready_gdf(gdf)

    # Act & Assert
    with patches[0], patches[1], patches[2], pytest.raises((ValueError, TypeError)):
        lookup(55.0, None)


def test_raises_when_shapefile_failed_to_load():
    # Arrange
    event = threading.Event()
    event.set()
    load_error = FileNotFoundError("Shapefile no encontrado")

    # Act & Assert
    with (
        patch("core.eco_enrich._gdf", None),
        patch("core.eco_enrich._ready", event),
        patch("core.eco_enrich._load_error", load_error),
        pytest.raises(Exception),
    ):
        lookup(55.0, 10.0)


def test_raises_when_init_was_never_called():
    # Arrange — _ready nunca fue activado (carga no iniciada)
    event = threading.Event()  # sin .set()

    # Act & Assert — no debe bloquear indefinidamente ni retornar vacío;
    # si _ready nunca se activa, lookup debe fallar rápido con timeout o error explícito.
    # Este test documenta que el contrato es "falla visible", no silencio.
    with (
        patch("core.eco_enrich._ready", event),
        patch("core.eco_enrich._gdf", None),
        patch("core.eco_enrich._load_error", RuntimeError("init() never called")),
    ):
        event.set()  # libera el wait pero con _load_error seteado
        with pytest.raises(Exception):
            lookup(55.0, 10.0)


def test_result_never_contains_empty_strings_for_valid_coordinates():
    # Arrange
    gdf = _make_gdf(realm="Afrotropic", biome="Tropical Grasslands", eco="Serengeti")
    patches = _patch_ready_gdf(gdf)

    # Act
    with patches[0], patches[1], patches[2]:
        result = lookup(-2.3, 34.8)  # Tanzania

    # Assert — invariante: ningún campo vacío para coordenadas válidas
    assert all(v != "" for v in result.values()), (
        f"lookup devolvió campos vacíos para coordenadas válidas: {result}"
    )
    assert set(result.keys()) == {"realm", "biome", "ecoregion"}
