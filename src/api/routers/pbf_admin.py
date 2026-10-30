"""pbf_admin.py — Technical tool: read a Geofabrik .osm.pbf, pick a country and export admin boundaries as TopoJSON.

Flow:
    POST /technical/pbf/open     {path}                      -> token + countries + admin levels in the file
    POST /technical/pbf/preview  {token, country, levels, simplify} -> simplified GeoJSON for the map
    POST /technical/pbf/export   {token, country, levels, simplify} -> TopoJSON download

The PBF is read from a path on this machine (the files are too big to upload through the browser).
The first preview/export for a country runs GDAL once and keeps the extracted country boundaries in a
pickle cache, so changing the simplification does not read the PBF again.
"""

from __future__ import annotations

import hashlib
import json
import math
import pickle
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

router = APIRouter()

MAX_DATASETS = 3
PREVIEW_PCT = 5
CACHE_DIR = Path(tempfile.gettempdir()) / "geoguessr-pbf-admin"
_LEVEL_COLORS = (
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
)

_DATASETS: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()
_COMPUTE = threading.Lock()


# ── helpers ────────────────────────────────────────────────────────────────


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _parse_other_tags(raw) -> dict[str, str]:
    if not isinstance(raw, str) or not raw:
        return {}
    return dict(re.findall(r'"([^"]+)"=>"([^"]*)"', raw))


def _level(value) -> int | None:
    text = _clean(value).strip()
    return int(text) if text.isdigit() else None


def _polys(geom):
    if geom is None or geom.is_empty:
        return None
    parts = [p for p in shapely.get_parts(geom) if p.geom_type == "Polygon" and not p.is_empty]
    if not parts:
        return None
    return shapely.union_all(parts)


def _country_code(tags: dict[str, str]) -> str:
    for key in ("ISO3166-1:alpha2", "ISO3166-1"):
        value = tags.get(key, "").strip().upper()
        if len(value) == 2 and value.isalpha():
            return value
    return ""


def _cache_file(token: str, country: str, levels: list[int]) -> Path:
    key = f"{token}|{country}|{','.join(map(str, levels))}"
    digest = hashlib.sha1(key.encode()).hexdigest()
    return CACHE_DIR / f"{token}-{digest}.pkl"


def _drop_cache(token: str) -> None:
    for path in CACHE_DIR.glob(f"{token}-*.pkl"):
        path.unlink(missing_ok=True)


def _dataset(token: str) -> dict:
    with _LOCK:
        ds = _DATASETS.get(token)
        if ds is None:
            raise HTTPException(410, "That PBF expired. Open the file again.")
        return ds


# ── reading the file ───────────────────────────────────────────────────────


def _scan(path: Path) -> tuple[list[dict], list[dict]]:
    try:
        import osmium
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The osmium package is not installed on the server.") from e

    countries: list[dict] = []
    seen: set[str] = set()
    seen_codes: set[str] = set()
    counts: dict[int, int] = {}

    for obj in osmium.FileProcessor(str(path), osmium.osm.RELATION):
        if not obj.is_relation():
            continue
        tags = obj.tags
        if tags.get("boundary") != "administrative":
            continue
        if tags.get("type") == "multilinestring":  # border lines between countries, not areas
            continue
        level = _level(tags.get("admin_level"))
        if level is None:
            continue
        counts[level] = counts.get(level, 0) + 1
        if level != 2:
            continue
        name = tags.get("name:en") or tags.get("name")
        if not name:
            continue
        osm_id = str(obj.id)
        if osm_id in seen:
            continue
        seen.add(osm_id)
        code = _country_code(tags)
        if code and code in seen_codes:
            continue
        if code:
            seen_codes.add(code)
        countries.append({"id": osm_id, "name": str(name), "code": code})

    countries.sort(key=lambda c: c["name"].casefold())
    levels = [{"level": level, "count": counts[level]} for level in sorted(counts)]
    return countries, levels


def _open(path_text: str, temporary: bool = False, display_name: str | None = None) -> dict:
    path = Path(path_text.strip().strip('"'))
    if not path.is_file():
        raise HTTPException(404, f"No file at {path}")
    if not path.name.lower().endswith(".pbf"):
        raise HTTPException(422, "That is not an .osm.pbf file")
    countries, levels = _scan(path)
    if not countries:
        raise HTTPException(
            422,
            "No country boundaries (boundary=administrative, admin_level=2) were found in this file.",
        )
    token = uuid4().hex
    ds = {
        "token": token,
        "path": path,
        "name": display_name or path.name,
        "size": path.stat().st_size,
        "countries": countries,
        "levels": levels,
        "temporary": temporary,
    }
    with _LOCK:
        _DATASETS[token] = ds
        while len(_DATASETS) > MAX_DATASETS:
            old, old_ds = _DATASETS.popitem(last=False)
            _drop_cache(old)
            if old_ds.get("temporary"):
                old_ds["path"].unlink(missing_ok=True)
    return {
        "token": token,
        "file": {"name": ds["name"], "size": ds["size"]},
        "countries": countries,
        "levels": levels,
    }


# ── extracting one country ─────────────────────────────────────────────────


def _read_admin(path: Path, levels: list[int]):
    try:
        import pyogrio
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The pyogrio package is not installed on the server.") from e

    wanted = sorted(set(levels) | {2})
    levels_sql = ", ".join(f"'{level}'" for level in wanted)
    where = f"boundary = 'administrative' AND admin_level IN ({levels_sql})"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return pyogrio.read_dataframe(
                path,
                layer="multipolygons",
                where=where,
                columns=["osm_id", "name", "admin_level", "other_tags"],
            )
        except Exception as e:
            raise HTTPException(422, f"GDAL could not read the boundaries in this file: {e}") from e


def _extract(ds: dict, country: str, levels: list[int]) -> dict:
    gdf = _read_admin(ds["path"], levels)
    if gdf.empty:
        raise HTTPException(422, "No administrative boundaries were found in this file.")

    ids = gdf["osm_id"].map(_clean)
    admin_levels = gdf["admin_level"].map(_level)
    mask = (admin_levels == 2) & (ids == country)
    if not mask.any():
        raise HTTPException(404, "That country is no longer in the open file.")

    country_parts = [shapely.make_valid(g) for g in gdf.loc[mask, "geometry"] if g is not None]
    country_geom = _polys(shapely.union_all(country_parts))
    if country_geom is None:
        raise HTTPException(422, "That country has no usable outline in this file.")

    geoms = gdf.geometry.to_numpy()
    present = np.array([g is not None and not g.is_empty for g in geoms], dtype=bool)
    inside = np.zeros(len(geoms), dtype=bool)
    if present.any():
        points = shapely.point_on_surface(geoms[present])
        inside[present] = shapely.contains_xy(
            country_geom, shapely.get_x(points), shapely.get_y(points)
        )

    wanted = set(levels)
    keep = inside & np.array([level in wanted for level in admin_levels], dtype=bool)
    keep &= ~((admin_levels == 2) & (ids != country))

    features: list[tuple[dict, object]] = []
    counts: dict[int, int] = {}
    for i in np.nonzero(keep)[0]:
        geom = geoms[i]
        if geom is None or geom.is_empty:
            continue
        if not geom.is_valid:
            geom = _polys(shapely.make_valid(geom))
            if geom is None:
                continue
        level = int(admin_levels.iloc[i])
        row = gdf.iloc[i]
        tags = _parse_other_tags(row["other_tags"])
        name = _clean(row["name"]) or tags.get("name:en") or tags.get("name") or "?"
        iso = tags.get("ISO3166-2") or tags.get("ISO3166-1:alpha2") or tags.get("ISO3166-1") or ""
        props = {
            "name": str(name),
            "admin_level": level,
            "osm_id": _clean(row["osm_id"]),
            "iso": str(iso),
        }
        features.append((props, geom))
        counts[level] = counts.get(level, 0) + 1

    if not features:
        raise HTTPException(422, "No boundaries of the selected levels fall inside this country.")

    features.sort(key=lambda item: (item[0]["admin_level"], -item[1].area))
    data = {"features": features, "counts": counts}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _cache_file(ds["token"], country, levels).open("wb") as f:
        pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    return data


def _load(ds: dict, country: str, levels: list[int]) -> dict:
    path = _cache_file(ds["token"], country, levels)
    if path.is_file():
        with path.open("rb") as f:
            return pickle.load(f)
    with _COMPUTE:
        if path.is_file():
            with path.open("rb") as f:
                return pickle.load(f)
        return _extract(ds, country, levels)


# ── preview / export ───────────────────────────────────────────────────────


def _tolerance(features: list[tuple[dict, object]], pct: int) -> float:
    main = max(features, key=lambda item: item[1].area)[1]
    minx, miny, maxx, maxy = main.bounds
    diag = math.hypot(maxx - minx, maxy - miny)
    return max(diag, 1e-9) * (pct / 100) * 0.01


def _simplify(geom, tol: float):
    if tol <= 0:
        return geom
    try:
        out = shapely.simplify(geom, tol, preserve_topology=True)
    except Exception:  # noqa: BLE001
        return geom
    return out if out is not None and not out.is_empty else geom


def _color(level: int) -> str:
    return _LEVEL_COLORS[level % len(_LEVEL_COLORS)]


def _preview(ds: dict, country: str, levels: list[int], simplify: int) -> dict:
    data = _load(ds, country, levels)
    pct = max(PREVIEW_PCT, simplify)
    tol = _tolerance(data["features"], pct)
    out = []
    for props, geom in data["features"]:
        level = props["admin_level"]
        out.append(
            {
                "type": "Feature",
                "properties": {
                    **props,
                    "tag": f"ADM{level} · {props['name']}",
                    "color": _color(level),
                    "shade": 0,
                },
                "geometry": json.loads(shapely.to_geojson(_simplify(geom, tol))),
            }
        )
    counts = [{"level": level, "count": data["counts"][level]} for level in sorted(data["counts"])]
    return {
        "geojson": {"type": "FeatureCollection", "features": out},
        "counts": counts,
        "total": len(out),
        "simplified_pct": pct,
        "tolerance_deg": tol,
    }


def _export(ds: dict, country: str, levels: list[int], simplify: int) -> tuple[str, str]:
    try:
        from topojson import Topology
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The topojson package is not installed on the server.") from e

    data = _load(ds, country, levels)
    tol = 0 if simplify <= 0 else _tolerance(data["features"], simplify)
    feats = [
        {
            "type": "Feature",
            "properties": props,
            "geometry": json.loads(shapely.to_geojson(_simplify(geom, tol))),
        }
        for props, geom in data["features"]
    ]
    topology = Topology(
        {"type": "FeatureCollection", "features": feats},
        object_name="ADM",
    )
    code = next((c["code"] for c in ds["countries"] if c["id"] == country), "")
    label = code or country
    levels_text = "-".join(map(str, levels))
    return topology.to_json(), f"{label}-ADM{levels_text}.topojson"


# ── API ────────────────────────────────────────────────────────────────────


class OpenBody(BaseModel):
    path: str = Field(min_length=1)


class ExtractBody(BaseModel):
    token: str
    country: str
    levels: list[int] = Field(min_length=1)
    simplify: int = Field(0, ge=0, le=100)


def _levels(values: list[int]) -> list[int]:
    levels = sorted({int(v) for v in values if 1 <= int(v) <= 20})
    if not levels:
        raise HTTPException(422, "Pick at least one admin level")
    return levels


@router.post("/technical/pbf/open")
async def open_pbf(body: OpenBody):
    return await run_in_threadpool(_open, body.path)


@router.post("/technical/pbf/upload")
async def upload_pbf(request: Request, filename: str | None = None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"upload-{uuid4().hex}.osm.pbf"
    try:
        with path.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if path.stat().st_size == 0:
            raise HTTPException(422, "Empty file")
        return await run_in_threadpool(_open, str(path), True, filename)
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.post("/technical/pbf/preview")
async def preview_pbf(body: ExtractBody):
    return await run_in_threadpool(
        _preview, _dataset(body.token), body.country, _levels(body.levels), body.simplify
    )


@router.post("/technical/pbf/export")
async def export_pbf(body: ExtractBody):
    content, filename = await run_in_threadpool(
        _export, _dataset(body.token), body.country, _levels(body.levels), body.simplify
    )
    return Response(
        content=content,
        media_type="application/topojson+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
