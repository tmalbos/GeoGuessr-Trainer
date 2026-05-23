"""
eco_enrich.py
Lookup de ecoregión, bioma y reino a partir de coordenadas.

Estrategia: point-in-polygon directo contra el shapefile de RESOLVE Ecoregions 2017.
El shapefile se carga una sola vez en background al arrancar la aplicación.
Cualquier llamada a `lookup()` antes de que termine la carga simplemente espera.
"""

import threading
from pathlib import Path

# Resultado vacío reutilizable
_EMPTY = {"realm": "", "biome": "", "ecoregion": ""}

# Estado compartido entre el hilo de carga y el hilo principal
_gdf = None  # GeoDataFrame cargado
_load_error = None  # excepción si falló la carga
_ready = threading.Event()  # se activa cuando la carga termina (ok o error)
_load_lock = threading.Lock()  # evita doble carga si se llama init() dos veces

SHP_PATH = Path("Ecoregions2017/Ecoregions2017.shp")


def _load(path: Path) -> None:
    """Carga el shapefile en el hilo de background."""
    global _gdf, _load_error
    try:
        import geopandas as gpd

        gdf = gpd.read_file(path)[["ECO_NAME", "BIOME_NAME", "REALM", "geometry"]]
        # Reproyectar a WGS84 para que las coordenadas lat/lon funcionen directamente
        gdf = gdf.to_crs(epsg=4326)
        # Índice espacial (STRtree) — se construye implícitamente en geopandas >= 0.10
        # La primera consulta lo construye; hacemos una consulta dummy para forzarlo ahora
        from shapely.geometry import Point

        _ = gdf[gdf.geometry.contains(Point(0, 0))]
        _gdf = gdf
    except Exception as e:
        _load_error = e
    finally:
        _ready.set()


def init() -> None:
    """
    Dispara la carga del shapefile en un hilo daemon.
    Llamar una sola vez al arrancar la aplicación.
    Si ya fue llamado, no hace nada.
    """
    with _load_lock:
        if _ready.is_set():
            return  # ya cargado (o ya falló)
        if not SHP_PATH.exists():
            # Marcamos como listo con error para no bloquear llamadas futuras
            global _load_error
            _load_error = FileNotFoundError(
                f"Shapefile no encontrado: {SHP_PATH.resolve()}\n"
                "Descargalo desde https://ecoregions.appspot.com/ y ubicalo en Ecoregions2017/"
            )
            _ready.set()
            return

        t = threading.Thread(target=_load, args=(SHP_PATH,), daemon=True, name="eco-loader")
        t.start()


def lookup(lat: float, lon: float) -> dict[str, str]:
    if lat is None or lon is None:
        raise ValueError(f"lookup() requires valid coordinates, got lat={lat!r} lon={lon!r}")

    _ready.wait()

    if _load_error is not None:
        raise RuntimeError("eco_enrich: shapefile could not be loaded") from _load_error

    if _gdf is None:
        raise RuntimeError("eco_enrich: _gdf is None after successful load — internal bug")

    from shapely.geometry import Point

    point = Point(lon, lat)

    idxs = list(_gdf.sindex.query(point, predicate="intersects"))
    candidates = _gdf.iloc[idxs] if idxs else _gdf.iloc[[]]

    if candidates.empty:
        nearest = _gdf.sindex.nearest(point)

        while hasattr(nearest, "__iter__"):
            nearest = next(iter(nearest))

        nearest_idx = int(nearest)
        candidates = _gdf.iloc[[nearest_idx]]

    row = candidates.iloc[0]
    return {
        "realm": row["REALM"],
        "biome": row["BIOME_NAME"],
        "ecoregion": row["ECO_NAME"],
    }


def is_ready() -> bool:
    """True si la carga ya terminó (útil para mostrar estado en la UI)."""
    return _ready.is_set()


def load_error() -> Exception | None:
    """Devuelve la excepción de carga si hubo alguna, o None."""
    return _load_error
