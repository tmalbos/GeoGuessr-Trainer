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


def _cache_file(token: str, country: str) -> Path:
    key = f"{token}|{country}"
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


def _scan(path: Path) -> tuple[list[dict], list[dict], dict[int, list[int]]]:
    try:
        import osmium
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The osmium package is not installed on the server.") from e

    countries: list[dict] = []
    seen: set[str] = set()
    seen_codes: set[str] = set()
    counts: dict[int, int] = {}
    ids: dict[int, list[int]] = {}

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
        ids.setdefault(level, []).append(obj.id)
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
    return countries, levels, ids


def _open(path_text: str, temporary: bool = False, display_name: str | None = None) -> dict:
    path = Path(path_text.strip().strip('"'))
    if not path.is_file():
        raise HTTPException(404, f"No file at {path}")
    if not path.name.lower().endswith(".pbf"):
        raise HTTPException(422, "That is not an .osm.pbf file")
    countries, levels, ids = _scan(path)
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
        "ids": ids,
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


class _AreaCollector:
    """Second-pass handler: turns every assembled admin relation into WKB."""

    def __init__(self) -> None:
        from osmium.geom import WKBFactory

        self._factory = WKBFactory()
        self.areas: list[tuple[int, dict[str, str], bytes]] = []

    def area(self, area) -> None:
        if area.from_way():
            return
        tags = area.tags
        if tags.get("boundary") != "administrative":
            return
        if _level(tags.get("admin_level")) is None:
            return
        try:
            wkb = self._factory.create_multipolygon(area)
        except Exception:  # noqa: BLE001
            return
        self.areas.append(((area.id - 1) // 2, dict(tags), wkb))


def _read_admin(path: Path, ids: list[int]) -> list[tuple[int, dict[str, str], object]]:
    try:
        import osmium
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The osmium package is not installed on the server.") from e

    # First pass: only the relations we want become area candidates.
    manager = osmium.area.AreaManager()
    keep = osmium.filter.IdFilter(ids)
    keep.enable_for(osmium.osm.RELATION)
    pool = osmium.io.ThreadPool()
    with osmium.io.Reader(str(path), osmium.osm.RELATION, thread_pool=pool) as reader:
        osmium.apply(reader, keep, manager.first_pass_handler())

    # Second pass: assemble those areas (needs node locations for the way members).
    collector = _AreaCollector()
    locations = osmium.helper.NodeLocationsForWays(osmium.index.create_map("flex_mem"))
    locations.ignore_errors()
    entities = osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION
    with osmium.io.Reader(str(path), entities, thread_pool=pool) as reader:
        osmium.apply(reader, locations, manager.second_pass_handler(collector))

    out: list[tuple[int, dict[str, str], object]] = []
    for rel_id, tags, wkb in collector.areas:
        try:
            geom = shapely.from_wkb(wkb)
        except Exception:  # noqa: BLE001
            continue
        if geom is None or geom.is_empty:
            continue
        out.append((rel_id, tags, geom))
    return out


def _local_outline(code: str):
    """The app's own outline for the country, for extracts that cut a country's boundary relation."""
    if not code:
        return None
    path = Path(__file__).resolve().parents[3] / "data" / "geo" / "countries.parquet"
    if not path.is_file():
        return None
    try:
        import geopandas as gpd

        rows = gpd.read_parquet(path, columns=["code", "geometry"])
        hit = rows[rows["code"] == code]
        if not len(hit):
            return None
        return _polys(shapely.make_valid(hit.geometry.iloc[0]))
    except Exception:  # noqa: BLE001
        return None


def _extract(ds: dict, country: str) -> dict:
    try:
        cid = int(country)
    except ValueError as e:
        raise HTTPException(404, "That country is no longer in the open file.") from e

    ids = [rel for rels in ds["ids"].values() for rel in rels]
    if cid not in ids:
        raise HTTPException(404, "That country is no longer in the open file.")

    areas = _read_admin(ds["path"], ids)
    if not areas:
        raise HTTPException(422, "No administrative boundaries were found in this file.")

    country_geom = None
    for rel_id, _, geom in areas:
        if rel_id == cid:
            country_geom = _polys(shapely.make_valid(geom))
            break
    if country_geom is None:
        code = next((c["code"] for c in ds["countries"] if c["id"] == country), "")
        country_geom = _local_outline(code)
    if country_geom is None:
        raise HTTPException(422, "That country has no usable outline in this file.")

    features: list[tuple[dict, object]] = []
    counts: dict[int, int] = {}
    for rel_id, tags, geom in areas:
        level = _level(tags.get("admin_level"))
        if level is None:
            continue
        if not geom.is_valid:
            geom = _polys(shapely.make_valid(geom))
            if geom is None:
                continue
        point = shapely.point_on_surface(geom)
        if not country_geom.contains(point):
            continue
        name = tags.get("name:en") or tags.get("name") or "?"
        iso = tags.get("ISO3166-2") or tags.get("ISO3166-1:alpha2") or tags.get("ISO3166-1") or ""
        props = {
            "name": str(name),
            "admin_level": level,
            "osm_id": str(rel_id),
            "iso": str(iso),
        }
        features.append((props, geom))
        counts[level] = counts.get(level, 0) + 1

    if not features:
        raise HTTPException(422, "No boundaries of the selected levels fall inside this country.")

    features.sort(key=lambda item: (item[0]["admin_level"], -item[1].area))
    data = {"features": features, "counts": counts}
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    with _cache_file(ds["token"], country).open("wb") as f:
        pickle.dump(data, f, protocol=pickle.HIGHEST_PROTOCOL)
    return data


def _load(ds: dict, country: str) -> dict:
    path = _cache_file(ds["token"], country)
    if path.is_file():
        with path.open("rb") as f:
            return pickle.load(f)
    with _COMPUTE:
        if path.is_file():
            with path.open("rb") as f:
                return pickle.load(f)
        return _extract(ds, country)


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


def _available(data: dict) -> list[dict]:
    return [{"level": level, "count": data["counts"][level]} for level in sorted(data["counts"])]


def _selected(data: dict, levels: list[int]) -> list[tuple[dict, object]]:
    wanted = set(levels)
    return [item for item in data["features"] if item[0]["admin_level"] in wanted]


def _preview(ds: dict, country: str, levels: list[int], simplify: int) -> dict:
    data = _load(ds, country)
    available = _available(data)
    chosen = _selected(data, levels)
    if not chosen:
        return {
            "geojson": {"type": "FeatureCollection", "features": []},
            "levels": available,
            "counts": [],
            "total": 0,
            "simplified_pct": PREVIEW_PCT,
            "tolerance_deg": 0,
        }
    pct = max(PREVIEW_PCT, simplify)
    tol = _tolerance(chosen, pct)
    out = []
    for props, geom in chosen:
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
    return {
        "geojson": {"type": "FeatureCollection", "features": out},
        "levels": available,
        "counts": [
            {"level": level, "count": data["counts"][level]} for level in sorted(set(levels))
        ],
        "total": len(out),
        "simplified_pct": pct,
        "tolerance_deg": tol,
    }


def _export(ds: dict, country: str, levels: list[int], simplify: int) -> tuple[str, str]:
    try:
        from topojson import Topology
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The topojson package is not installed on the server.") from e

    data = _load(ds, country)
    chosen = _selected(data, levels)
    if not chosen:
        raise HTTPException(422, "Pick at least one admin level")
    tol = 0 if simplify <= 0 else _tolerance(chosen, simplify)
    feats = [
        {
            "type": "Feature",
            "properties": props,
            "geometry": json.loads(shapely.to_geojson(_simplify(geom, tol))),
        }
        for props, geom in chosen
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
    levels: list[int] = Field(default_factory=list)
    simplify: int = Field(0, ge=0, le=100)


def _levels(values: list[int]) -> list[int]:
    return sorted({int(v) for v in values if 1 <= int(v) <= 20})


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
