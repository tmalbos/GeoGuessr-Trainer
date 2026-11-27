"""clues.py — Data layer for per-country clues: the yaml, its files, medals and read access."""

import csv
import io
import json
import logging
import re
import threading
import time
from pathlib import Path

import geopandas as gpd
import shapely
import yaml
from fastapi import HTTPException, Request

from src.api.deps import get_ctx
from src.explore.country_clues.badges import medal_for
from src.explore.country_clues.edit_mode.uploads.csvs import AreasError, build_areas, read_rows
from src.explore.country_clues.regional_stain_areas import _borders, _outline
from src.geo.local_geo import CountryAreaMissingError
from src.geo.pinpoint import area_km2
from src.geo.prefix_levels import build_levels

logger = logging.getLogger("uvicorn.error")

CLUES_DIR = Path(__file__).parents[3] / "data" / "clues"
STAGING = CLUES_DIR / ".staging"

# Keep in sync with frontend/src/pages/Explore/clues/tags.js
LOCATION_TAGS = ["Urban", "Rural", "Anywhere"]
GENERAL_TAGS = [
    "Architecture", "Phone Numbers", "Postal Codes", "Landscape", "Vegetation",
    "Infrastructure", "Vehicle", "License Plate", "Flags", "Roads", "Subdivision Names",
    "City Names", "Agriculture", "Soil", "Brand", "Language", "Script", "Road Sign",
    "Electricity Pole", "Bollard", "National Parks", "Biome", "Religion",
    "Animals", "Google Car", "Copyright", "Camera Generation", "Camera", "Coverage",
]  # fmt: skip

KINDS = ("image", "svg", "csv", "polygons", "lines", "points")

AREA_KINDS = ("svg", "csv", "polygons", "lines", "points")  # what says which part of the country
GEOM_KINDS = ("polygons", "lines", "points")
STAGE_KINDS = ("image", "csv", "geometry")

_LOCK = threading.Lock()

# The overlay of a clue sits next to its geometry file and shares its name: reg_roads_00003.overlay.svg
# So do the per-depth maps of a prefix-coloured clue (see prefix_levels.py): reg_phonenumbers_00003.d1.parquet
ASSET_RE = re.compile(r"(nat|reg)_[a-z]+_\d{5}(\.(?:overlay|d\d{1,2}))?\.(webp|svg|csv|parquet)")
TOKEN_RE = re.compile(r"[0-9a-f]{32}")

# Countries that have at least one clue. Built from disk on first request,
# then kept in memory for the life of the server (never written to disk) and updated by each change.
_SUM_LOCK = threading.Lock()
_HAS: set[str] | None = None


def _summary() -> set[str]:
    global _HAS  # noqa: PLW0603
    with _SUM_LOCK:
        if _HAS is None:
            _HAS = {
                d.name.upper()
                for d in sorted(CLUES_DIR.glob("??"))
                if d.is_dir() and re.fullmatch(r"[A-Za-z]{2}", d.name) and _load(d.name)
            }
        return set(_HAS)


def _set_has(cc: str, items: list[dict]) -> None:
    """Called after a country's clues change: only that country's entry is touched, nothing is rescanned."""
    with _SUM_LOCK:
        if _HAS is None:  # not built yet; the first request will read the final state from disk
            return
        (_HAS.add if items else _HAS.discard)(cc)


def _country_km2(local, code: str) -> float:
    """Country area (km²) stored in the national parquet by scripts/convert_geo.py.

    Raises CountryAreaMissingError when the file predates that column or has no positive value.
    """
    layer = local._admin_layer(code)  # noqa: SLF001
    if layer is None or not len(layer) or "country_km2" not in layer.columns:
        raise CountryAreaMissingError(code)
    try:
        value = float(layer["country_km2"].iloc[0])
    except (TypeError, ValueError):
        raise CountryAreaMissingError(code) from None
    if value <= 0:
        raise CountryAreaMissingError(code)
    return value


def _apply_medal(cc: str, entry: dict, request: Request, folder: Path, override=None) -> None:
    """Store `score` and `medal` on a clue. Computed once, when the clue is saved (see badges.py).

    A regional clue whose country area is not stored (see scripts/backfill_country_area.py) is logged
    and left unscored.
    """
    local = get_ctx(request).local_geo
    try:
        # Only a regional clue needs the country's area, stored in the national parquet
        # (scripts/convert_geo.py).
        country = None
        if local is not None and entry.get("scope") == "reg":
            country = _country_km2(local, cc)
        res = medal_for(cc, entry, ClueSource(local), folder, override, country_km2=country)
    except Exception:
        logger.exception("Could not score clue %s/%s", cc, entry.get("id"))
        return
    if res is None:
        return
    entry["score"] = res[0]
    if res[1]:
        entry["medal"] = res[1]


def _restandardize(path: Path) -> None:
    """Re-check the colors of a drawn-areas file before it is saved. Never blocks the save."""
    from src.explore.country_clues.edit_mode.draw.canvas import (
        restandardize,
    )

    try:
        restandardize(path)
    except Exception:
        logger.exception("Could not standardize the colors of %s", path.name)


def _cc(code: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{2}", code):
        raise HTTPException(422, "Invalid country code")
    return code.upper()


def _yaml_path(cc: str) -> Path:
    return CLUES_DIR / cc / "clues.yaml"


def _load(cc: str) -> list[dict]:
    p = _yaml_path(cc)
    if not p.is_file():
        return []
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise HTTPException(500, f"{p.name} for {cc} is corrupted") from e


def _save(cc: str, items: list[dict]) -> None:
    p = _yaml_path(cc)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8"
    )
    tmp.replace(p)


def _asset(cc: str, name: str) -> Path:
    if not ASSET_RE.fullmatch(name):
        raise HTTPException(422, "Invalid file name")
    p = CLUES_DIR / _cc(cc) / name
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return p


def _next_num(folder: Path, scope: str, general: str) -> int:
    """Lowest free number for this scope + category (gaps left by deleted clues are reused).
    One clue uses ONE number for all of its files.
    """
    key = "".join(general.split()).lower()
    prefix = f"{scope}_{key}_"
    pat = re.compile(re.escape(prefix) + r"(\d{5})\..+")
    used = {int(m.group(1)) for p in folder.glob(prefix + "*") if (m := pat.fullmatch(p.name))}
    num = 0
    while num in used:
        num += 1
    return num


def _next_name(folder: Path, scope: str, general: str, suffix: str, num: int | None = None) -> str:
    key = "".join(general.split()).lower()
    if num is None:
        num = _next_num(folder, scope, general)
    return f"{scope}_{key}_{num:05d}{suffix}"


def _staged_path(token: str, kind: str) -> Path:
    if not TOKEN_RE.fullmatch(token) or kind not in KINDS:
        raise HTTPException(422, "Invalid staged file")
    hits = list(STAGING.glob(f"{token}.{kind}.*"))
    if not hits:
        raise HTTPException(410, f"The uploaded {kind} file expired. Load it again.")
    return hits[0]


def _purge_staging() -> None:
    cutoff = time.time() - 86400
    for p in STAGING.glob("*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


def _overlay_name(geo_name: str) -> str:
    """reg_roads_00003.parquet -> reg_roads_00003.overlay.svg."""
    return f"{Path(geo_name).stem}.overlay.svg"


def _write_overlay(folder: Path, name: str, svg: str) -> None:
    tmp = folder / f"{name}.tmp"
    tmp.write_text(svg, encoding="utf-8")
    tmp.replace(folder / name)


def _make_levels(folder: Path, entry: dict, geo_file: Path | None = None) -> dict[str, str] | None:
    """{depth: file name} of the per-depth maps of a prefix-coloured drawn clue, written next to its geometry file.
    None when the clue isn't one, or when they can't be made (the card then shows the drawing as it is).
    """
    if not entry.get("prefix_colors") or not entry.get("polygons"):
        return None
    try:
        levels = build_levels(geo_file or folder / entry["polygons"], folder, entry["polygons"])
    except Exception:
        logger.exception("Could not build the prefix levels of %s", entry["polygons"])
        return None
    return {str(d): n for d, n in levels.items()} or None


def _countries_with_clues() -> set[str]:
    """Every country that has at least one clue (hidden ones too: they can still be related)."""
    return {
        d.name.upper()
        for d in sorted(CLUES_DIR.glob("??"))
        if d.is_dir() and re.fullmatch(r"[A-Za-z]{2}", d.name) and _load(d.name)
    }


_SHAPES: dict[tuple, tuple | None] = {}
_PIECES: dict[tuple, list[tuple]] = {}
_NAMES: dict[tuple, list[tuple]] = {}
_SHADES: dict[tuple, list[tuple]] = {}


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

    def shade_pieces(self, cc: str, clue: dict) -> list[tuple]:
        """[(shade, geometry)], darkest first, of a clue whose polygons carry 2+ shades; [] for any other clue."""
        cc = cc.strip().upper()
        name = clue.get("polygons")
        if not name:
            return []
        path = CLUES_DIR / cc / name
        try:
            stamp = path.stat().st_mtime_ns
        except OSError:
            return []
        key = (cc, name, stamp)
        if key not in _SHADES:
            out = []
            try:
                gdf = gpd.read_parquet(path)
                if "shade" in gdf.columns and "name" not in gdf.columns:
                    shades = gdf["shade"].fillna(0).astype(int).to_numpy()
                    geoms = gdf.geometry.to_numpy()
                    out = [
                        (int(v), shapely.union_all(shapely.make_valid(geoms[shades == v])))
                        for v in sorted(set(shades.tolist()))
                    ]
            except Exception:
                logger.exception("Could not read the shades of %s", name)
                out = []
            _SHADES[key] = out if len(out) > 1 else []
        return _SHADES[key]

    def depth_pieces(self, cc: str, clue: dict) -> list[tuple[int, list[tuple]]]:
        """[(depth, pieces)], lowest depth first, of a prefix-coloured clue ([] for any other clue)."""
        cc = cc.strip().upper()
        out = []
        levels = clue.get("levels") if clue.get("prefix_colors") else None
        for depth, name in sorted((levels or {}).items(), key=lambda kv: int(kv[0])):
            path = CLUES_DIR / cc / name
            try:
                stamp = path.stat().st_mtime_ns
            except OSError:
                continue
            key = (cc, name, stamp)
            if key not in _PIECES:
                try:
                    _PIECES[key] = self._named(cc, path, True)
                except Exception:
                    logger.exception("Could not read the areas of %s", name)
                    _PIECES[key] = []
            if _PIECES[key]:
                out.append((int(depth), _PIECES[key]))
        return out

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


_BASE: dict[str, dict] = {}


def _geojson(g):
    if g is None or g.is_empty:
        return None
    return json.loads(shapely.to_geojson(g))


def base(cc: str, local) -> dict:
    """{outline, borders} of a country, both lon/lat GeoJSON, cached for the life of the server."""
    cc = cc.upper()
    if cc in _BASE:
        return _BASE[cc]
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    outline = _outline(local, cc)
    borders = _borders(local, cc) if outline is not None else None
    _BASE[cc] = {"outline": _geojson(outline), "borders": _geojson(borders)}
    return _BASE[cc]
