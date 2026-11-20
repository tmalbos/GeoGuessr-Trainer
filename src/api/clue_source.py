"""clue_source.py — Read access to each country's clues and to the polygon a regional clue covers.

polygons clue  the geometry parquet of the clue, unioned.
csv clue       the first-level regions named in its CSV (column 2), taken from the local admin layer.
Everything else (svg, lines, points, images alone) has no polygon and is ignored.
Shapes are cached for the life of the server, keyed by file and modification time.
"""

import csv
import io
import logging

import geopandas as gpd
import shapely

from src.analysis.report_actions import area_km2
from src.api.routers.clues import CLUES_DIR, _load  # noqa: PLC2701
from src.geo.csv_areas import AreasError, build_areas, read_rows

logger = logging.getLogger("uvicorn.error")

_SHAPES: dict[tuple, tuple | None] = {}
_PIECES: dict[tuple, list[tuple]] = {}
_NAMES: dict[tuple, list[tuple]] = {}


def _norm(s) -> str:
    return str(s).strip().lower()


class ClueSource:
    def __init__(self, local_geo) -> None:
        self._local = local_geo

    def clues(self, cc: str) -> list[dict]:
        cc = (cc or "").strip().upper()
        return _load(cc) if len(cc) == 2 and cc.isalpha() else []

    def shape(self, cc: str, clue: dict) -> tuple | None:
        """(geometry, area km²) of a regional clue, or None when it has no polygon."""
        cc = cc.strip().upper()
        name = clue.get("polygons") or clue.get("csv")
        if not name:
            return None
        path = CLUES_DIR / cc / name
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return None
        key = (cc, name, stamp)
        if key not in _SHAPES:
            try:
                geom = self._polygons(path) if clue.get("polygons") else self._csv(cc, path)
            except Exception:
                logger.exception("Could not read the area of clue %s/%s", cc, clue.get("id"))
                geom = None
            ok = geom is not None and not geom.is_empty
            _SHAPES[key] = (geom, area_km2(geom)) if ok else None
        return _SHAPES[key]

    def pieces(self, cc: str, clue: dict) -> list[tuple]:
        """[(geometry, area km²)] of every separate area of a regional clue.

        A map-only clue (drawn areas, or a CSV of named groups) holds several named areas, each one
        a different answer, so they are NOT merged. Any other clue is a single piece (see shape).
        """
        cc = cc.strip().upper()
        name = clue.get("polygons") or clue.get("csv")
        if not name:
            return []
        path = CLUES_DIR / cc / name
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return []
        key = (cc, name, stamp)
        if key not in _PIECES:
            try:
                out = self._named(cc, path, bool(clue.get("polygons")))
            except Exception:
                logger.exception("Could not read the areas of clue %s/%s", cc, clue.get("id"))
                out = []
            if not out:
                one = self.shape(cc, clue)
                out = [one] if one else []
            _PIECES[key] = out
        return _PIECES[key]

    def levels(self, cc: str, clue: dict) -> list[tuple[int, list[tuple]]]:
        """[(depth, [(area name, geometry)])] of a prefix-coloured map-only clue, lowest depth first."""
        files = clue.get("levels") or {}
        if clue.get("image") or not files:
            return []
        return [(int(d), self.named(cc, clue, files[d])) for d in sorted(files, key=int)]

    def named(self, cc: str, clue: dict, file: str | None = None) -> list[tuple]:
        """[(area name, geometry)] of a map-only clue with named areas; [] for any other clue.

        `file` = the per-depth map to read instead of the clue's own geometry.
        """
        cc = cc.strip().upper()
        name = file or clue.get("polygons") or clue.get("csv")
        if not name or clue.get("image"):
            return []
        path = CLUES_DIR / cc / name
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return []
        key = (cc, name, stamp)
        if key not in _NAMES:
            try:
                _NAMES[key] = self._names(cc, path, bool(file or clue.get("polygons")))
            except Exception:
                logger.exception("Could not name the areas of clue %s/%s", cc, clue.get("id"))
                _NAMES[key] = []
        return _NAMES[key]

    def _names(self, cc: str, path, is_polygons: bool) -> list[tuple]:
        if is_polygons:
            gdf = gpd.read_parquet(path)
            if "name" not in gdf.columns:
                return []
            keys = gdf["name"].fillna("").astype(str)
            return [
                (n, shapely.union_all(shapely.make_valid(sub.geometry.to_numpy())))
                for n, sub in gdf.groupby(keys, sort=True)
                if n
            ]
        layer = self._local._admin_layer(cc)  # noqa: SLF001
        try:
            names, _, _, geoms = build_areas(read_rows(path.read_bytes()), layer, path.parent)
        except AreasError:
            return []
        return [(n, g) for n, g in zip(names, geoms, strict=True) if not g.is_empty]

    def _named(self, cc: str, path, is_polygons: bool) -> list[tuple]:
        """One (geometry, area) per named area; [] when the clue has no named areas."""
        if is_polygons:
            gdf = gpd.read_parquet(path)
            if "name" not in gdf.columns:
                return []
            names = gdf["name"].fillna("").astype(str)
            geoms = [
                shapely.union_all(shapely.make_valid(sub.geometry.to_numpy()))
                for _, sub in gdf.groupby(names, sort=True)
            ]
        else:
            layer = self._local._admin_layer(cc)  # noqa: SLF001
            try:
                geoms = build_areas(read_rows(path.read_bytes()), layer, path.parent)[3]
            except AreasError:
                return []
        return [(g, area_km2(g)) for g in geoms if not g.is_empty]

    @staticmethod
    def _polygons(path):
        gdf = gpd.read_parquet(path)
        return shapely.union_all(shapely.make_valid(gdf.geometry.to_numpy()))

    def _csv(self, cc: str, path):
        rows = list(csv.reader(io.StringIO(path.read_text(encoding="utf-8"))))
        names = {_norm(r[1]) for r in rows[1:] if len(r) > 1 and r[1].strip()}
        layer = self._local._admin_layer(cc)  # noqa: SLF001
        if not names or layer is None or "state" not in layer.columns:
            return None
        states = layer["state"].fillna("").astype(str).str.strip().str.lower()
        sel = layer[states.isin(names)]
        if sel.empty:
            return None
        return shapely.union_all(shapely.make_valid(sel.geometry.to_numpy()))
