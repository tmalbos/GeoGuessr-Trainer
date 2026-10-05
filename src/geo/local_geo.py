"""local_geo.py — Offline geo lookups from GeoParquet layers (biome, urban/rural, admin).

Layout under data/geo/ (built with scripts/convert_geo.py):
    biomes.parquet          BIOME_NAME, geometry
    urban.parquet           geometry
    countries.parquet       code, name, geometry   (only countries that have a local admin file)
    admin/{CC}.parquet      state, [subregion], city, geometry   (finest admin level, lazy-loaded)
"""

import threading
from pathlib import Path

import geopandas as gpd
import numpy as np
from shapely.geometry import Point, box

COUNTRY_TOL_DEG = 0.01  # ~1 km: absorbs simplification gaps without stealing neighbours' points
ADMIN_TOL_DEG = 0.05  # ~5 km: coasts / gaps between admin polygons, already inside the country


def _nearest_idx(gdf, point, max_distance: float | None = None) -> int | None:
    kwargs = {"return_all": False}
    if max_distance is not None:
        kwargs["max_distance"] = max_distance
    res = np.atleast_2d(gdf.sindex.nearest(point, **kwargs))
    if res.size == 0:
        return None
    return int(res[-1][0])


def _hit_idx(gdf, point, tol: float) -> int | None:
    idxs = gdf.sindex.query(point, predicate="intersects")
    if len(idxs):
        return int(idxs.min())
    return _nearest_idx(gdf, point, tol)


def _s(value) -> str | None:
    return value if isinstance(value, str) and value else None


def area_types(lats, lons, urban_gdf) -> list[str]:
    """Vectorised urban/rural classification for many points (used by the backfill)."""
    pts = gpd.points_from_xy(np.asarray(lons, dtype=float), np.asarray(lats, dtype=float))
    hit = urban_gdf.sindex.query(pts, predicate="intersects")
    urban = set(hit[0].tolist())
    return ["urban" if i in urban else "rural" for i in range(len(pts))]


class LocalGeo:
    def __init__(self, geo_dir: Path) -> None:
        self.dir = Path(geo_dir)
        self.biomes = None
        self.urban = None
        self.countries = None
        self._admin: dict[str, gpd.GeoDataFrame | None] = {}
        self._lock = threading.Lock()

    def _read(self, name: str, required: bool = True):
        path = self.dir / name
        if not path.exists():
            if required:
                msg = f"Missing {path}. Build it with scripts/convert_geo.py."
                raise FileNotFoundError(msg)
            return None
        gdf = gpd.read_parquet(path)
        _ = gdf.sindex  # build the spatial index now, not on the first query
        return gdf

    def load(self) -> None:
        """Blocking; run in an executor."""
        self.biomes = self._read("biomes.parquet")
        self.urban = self._read("urban.parquet")
        self.countries = self._read("countries.parquet", required=False)

    def _admin_layer(self, code: str):
        with self._lock:
            if code not in self._admin:
                path = self.dir / "admin" / f"{code}.parquet"
                if path.exists():
                    gdf = gpd.read_parquet(path)
                    _ = gdf.sindex
                    self._admin[code] = gdf
                else:
                    self._admin[code] = None
            return self._admin[code]

    def _locate_admin(self, point: Point) -> dict | None:
        """State / subregion / city from local files, or None if this point isn't covered."""
        if self.countries is None or self.countries.empty:
            return None
        ci = _hit_idx(self.countries, point, COUNTRY_TOL_DEG)
        if ci is None:
            return None
        code = self.countries["code"].iat[ci]
        layer = self._admin_layer(code)
        if layer is None:
            return None
        ai = _hit_idx(layer, point, ADMIN_TOL_DEG)
        if ai is None:
            return None
        sub = _s(layer["subregion"].iat[ai]) if "subregion" in layer.columns else None
        return {
            "country_code": code,
            "country": _s(self.countries["name"].iat[ci]) or code,
            "state": _s(layer["state"].iat[ai]) or "",
            "subregion": sub,
            "city": _s(layer["city"].iat[ai]) or "",
        }

    def _biome(self, point: Point) -> str:
        idxs = self.biomes.sindex.query(point, predicate="intersects")
        i = int(idxs.min()) if len(idxs) else _nearest_idx(self.biomes, point)
        return "" if i is None else self.biomes["BIOME_NAME"].iat[i]

    def _area(self, point: Point) -> str:
        hit = self.urban.sindex.query(point, predicate="intersects")
        return "urban" if len(hit) else "rural"

    def countries_in_bbox(self, west: float, south: float, east: float, north: float) -> list:
        """[(country code, outline)] for the locally-known countries touching the box."""
        if self.countries is None or self.countries.empty:
            return []
        idxs = self.countries.sindex.query(box(west, south, east, north), predicate="intersects")
        geoms = self.countries.geometry
        return [(self.countries["code"].iat[i], geoms.iat[i]) for i in idxs]

    def lookup(self, lat: float, lon: float) -> dict:
        """Admin is None when the point is not covered locally (caller falls back to Nominatim)."""
        point = Point(lon, lat)
        return {
            "admin": self._locate_admin(point),
            "biome": self._biome(point),
            "area_type": self._area(point),
        }
