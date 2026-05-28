"""eco_enrich.py
Lookup de ecoregión, bioma y reino a partir de coordenadas.

Estrategia: point-in-polygon directo contra un GeoDataFrame de
RESOLVE Ecoregions 2017 pasado como argumento.

Ya no hay estado global — la carga del shapefile es responsabilidad
de AppContext que pasa el GeoDataFrame a ``lookup()``.
"""


def lookup(lat: float, lon: float, gdf) -> dict[str, str]:
    if lat is None or lon is None:
        msg = f"lookup() requires valid coordinates, got lat={lat!r} lon={lon!r}"
        raise ValueError(msg)

    from shapely.geometry import Point

    point = Point(lon, lat)

    idxs = list(gdf.sindex.query(point, predicate="intersects"))
    candidates = gdf.iloc[idxs] if idxs else gdf.iloc[[]]

    if candidates.empty:
        nearest = gdf.sindex.nearest(point)

        while hasattr(nearest, "__iter__"):
            nearest = next(iter(nearest))

        nearest_idx = int(nearest)
        candidates = gdf.iloc[[nearest_idx]]

    row = candidates.iloc[0]
    return {
        "realm": row["REALM"],
        "biome": row["BIOME_NAME"],
        "ecoregion": row["ECO_NAME"],
    }
