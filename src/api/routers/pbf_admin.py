"""pbf_admin.py — Technical tool: read a Geofabrik .osm.pbf, pick a country and export admin boundaries as TopoJSON.

Flow:
    POST /technical/pbf/open     {path}                       -> token + countries in the file
    POST /technical/pbf/upload   (raw body, ?filename=)       -> same, for a file picked in the browser
    POST /technical/pbf/country  {token, country}             -> SLOW, once per country: builds every admin boundary of that
                                                                 country, returns the levels it really has (+ counts)
    POST /technical/pbf/preview  {token, country, levels, simplify} -> per-level GeoJSON features (fast, cached)
    POST /technical/pbf/export   {token, country, levels, simplify} -> TopoJSON download

How it works (why it is fast after the first load):
  1. Opening the file reads only the relations (cheap) and remembers each admin relation's member ways.
  2. Choosing a country reads the file once more (nodes + ways, node locations cached by osmium), keeps ONLY the
     ways that admin relations use (and, when the app has an outline for the country, only those near it),
     assembles the boundaries with shapely and keeps the ones whose inside point lies in the country.
  3. Everything lives in memory, grouped by level. Changing levels / simplification never touches the PBF again;
     simplified + serialised features are cached per (level, simplification).
"""

from __future__ import annotations

import json
import logging
import math
import tempfile
import threading
from array import array
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

log = logging.getLogger(__name__)
router = APIRouter()

MAX_DATASETS = 3
MAX_COUNTRIES = 3  # extracted countries kept in memory per file
MAX_PREVIEW_CACHE = 60  # (level, pct) entries per country
PREVIEW_PCT = 5
BBOX_PAD = 0.5  # degrees around the app's country outline when picking ways
FALLBACK_BUFFER = (
    0.05  # degrees: the app's outline is coarse, so widen it when it is used for "inside"
)
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
_COMPUTE = threading.Lock()  # one extraction at a time (CPU / memory heavy)
_OUTLINES: dict[str, object] = {}


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
    return parts[0] if len(parts) == 1 else shapely.union_all(parts)


def _country_code(tags) -> str:
    for key in ("ISO3166-1:alpha2", "ISO3166-1"):
        value = (tags.get(key) or "").strip().upper()
        if len(value) == 2 and value.isalpha():
            return value
    return ""


def _dataset(token: str) -> dict:
    with _LOCK:
        ds = _DATASETS.get(token)
        if ds is None:
            raise HTTPException(410, "That PBF expired. Open the file again.")
        _DATASETS.move_to_end(token)
        return ds


def _oneline(e: Exception) -> str:
    # api.js only shows backend text that is short, one line and not "technical"; keep it that way.
    text = f"{type(e).__name__}: {e}".replace("\n", " ").replace("Exception", "Error")
    return text[:140]


# ── step 1: open the file (relations only) ─────────────────────────────────


def _scan(path: Path) -> tuple[list[dict], dict[int, dict]]:
    try:
        import osmium
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The osmium package is not installed on the server.") from e

    countries: list[dict] = []
    seen_ids: set[str] = set()
    seen_codes: set[str] = set()
    rels: dict[int, dict] = {}

    for obj in osmium.FileProcessor(str(path), osmium.osm.RELATION):
        tags = obj.tags
        if tags.get("boundary") != "administrative":
            continue
        if tags.get("type") == "multilinestring":  # border lines between countries, not areas
            continue
        level = _level(tags.get("admin_level"))
        if level is None:
            continue
        name = tags.get("name:en") or tags.get("name") or ""

        if level == 2 and name and str(obj.id) not in seen_ids:
            seen_ids.add(str(obj.id))
            code = _country_code(tags)
            if not (code and code in seen_codes):
                if code:
                    seen_codes.add(code)
                countries.append({"id": str(obj.id), "name": str(name), "code": code})

        outer, inner = array("q"), array("q")
        for m in obj.members:
            if str(m.type) not in {"w", "way"}:
                continue
            (inner if m.role == "inner" else outer).append(m.ref)
        if outer:
            rels[obj.id] = {
                "level": level,
                "name": str(name or "?"),
                "iso": str(
                    tags.get("ISO3166-2")
                    or tags.get("ISO3166-1:alpha2")
                    or tags.get("ISO3166-1")
                    or ""
                ),
                "outer": outer,
                "inner": inner,
            }

    countries.sort(key=lambda c: c["name"].casefold())
    return countries, rels


def _open(path_text: str, temporary: bool = False, display_name: str | None = None) -> dict:
    path = Path(path_text.strip().strip('"'))
    if not path.is_file():
        raise HTTPException(404, f"No file at {path}")
    if not path.name.lower().endswith(".pbf"):
        raise HTTPException(422, "That is not an .osm.pbf file")
    try:
        countries, rels = _scan(path)
    except HTTPException:
        raise
    except Exception as e:
        log.exception("PBF scan failed")
        raise HTTPException(500, f"Could not read this file: {_oneline(e)}") from e
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
        "rels": rels,
        "temporary": temporary,
        "extracted": OrderedDict(),  # country id -> data
    }
    with _LOCK:
        _DATASETS[token] = ds
        while len(_DATASETS) > MAX_DATASETS:
            _, old = _DATASETS.popitem(last=False)
            if old.get("temporary"):
                old["path"].unlink(missing_ok=True)
    return {
        "token": token,
        "file": {"name": ds["name"], "size": ds["size"]},
        "countries": countries,
    }


# ── step 2: build one country ──────────────────────────────────────────────


def _local_outline(code: str):
    """The app's own (coarse) outline for the country: used for a bbox and as a fallback 'inside' test."""
    if not code:
        return None
    if code in _OUTLINES:
        return _OUTLINES[code]
    path = Path(__file__).resolve().parents[3] / "data" / "geo" / "countries.parquet"
    out = None
    if path.is_file():
        try:
            import geopandas as gpd

            rows = gpd.read_parquet(path, columns=["code", "geometry"])
            hit = rows[rows["code"] == code]
            if len(hit):
                out = _polys(shapely.union_all(shapely.make_valid(hit.geometry.to_numpy())))
        except Exception:
            log.warning("Could not read the local outline for %s", code, exc_info=True)
    _OUTLINES[code] = out
    return out


def _read_ways(path: Path, wanted: set[int], bbox) -> dict[int, np.ndarray]:
    """One pass over the file. Returns {way id: (n, 2) lon/lat array} for the wanted ways only.
    osmium filters out untagged nodes and unwanted ways before Python sees them (the node locations are still
    cached for the ways), so Python only touches the boundary ways.
    Ways with a missing node (extract edge) are skipped, and so are the relations that need them.
    """
    import osmium

    fp = osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY).with_locations()
    try:
        fp = fp.with_filter(osmium.filter.EmptyTagFilter().enable_for(osmium.osm.NODE))
        fp = fp.with_filter(osmium.filter.IdFilter(list(wanted)).enable_for(osmium.osm.WAY))
    except Exception:  # older pyosmium: slower, same result
        log.warning("osmium filters unavailable, reading without them", exc_info=True)

    out: dict[int, np.ndarray] = {}
    for o in fp:
        if not o.is_way() or o.id not in wanted:
            continue
        try:
            xy = np.array([(n.lon, n.lat) for n in o.nodes], dtype=float)
        except Exception:  # noqa: BLE001  # a node location is unknown
            continue
        if len(xy) < 2:
            continue
        if bbox is not None and (
            xy[:, 0].max() < bbox[0]
            or xy[:, 0].min() > bbox[2]
            or xy[:, 1].max() < bbox[1]
            or xy[:, 1].min() > bbox[3]
        ):
            continue
        out[o.id] = xy
    return out


def _faces(way_ids, ways: dict[int, np.ndarray]):
    """Filled polygon(s) of a set of ways. Fast path: the ways chain end to end into closed rings (the normal case)
    and no noding is needed. Otherwise the slower polygonize path.
    """
    lines = [shapely.linestrings(ways[i]) for i in way_ids if i in ways]
    if not lines:
        return None
    merged = shapely.line_merge(shapely.multilinestrings(lines))
    parts = shapely.get_parts(merged)
    if (
        len(parts)
        and bool(np.all(shapely.is_closed(parts)))
        and all(len(p.coords) >= 4 for p in parts)
    ):
        polys = [shapely.Polygon(p.coords) for p in parts]
        return (
            polys[0]
            if len(polys) == 1
            else shapely.union_all([g if g.is_valid else shapely.make_valid(g) for g in polys])
        )
    faces = [
        p
        for p in shapely.get_parts(
            shapely.polygonize(shapely.get_parts(shapely.line_merge(shapely.union_all(lines))))
        )
        if p.geom_type == "Polygon" and not p.is_empty
    ]
    return shapely.union_all(faces) if faces else None


def _assemble(rel: dict, ways: dict[int, np.ndarray]):
    """Outer rings (filled) minus inner rings. None when the relation is incomplete or its ways don't close."""
    if any(i not in ways for i in rel["outer"]):
        return None
    g = _faces(rel["outer"], ways)
    if g is None:
        return None
    if rel["inner"]:
        h = _faces(rel["inner"], ways)
        if h is not None:
            try:
                g = shapely.difference(g, h)
            except Exception:  # noqa: BLE001
                g = shapely.difference(shapely.make_valid(g), shapely.make_valid(h))
    if not shapely.is_valid(g):
        g = shapely.make_valid(g)
    return _polys(g)


def _extract(ds: dict, country: str) -> dict:
    cid_text = next((c["id"] for c in ds["countries"] if c["id"] == country), None)
    if cid_text is None:
        raise HTTPException(404, "That country is no longer in the open file.")
    cid = int(cid_text)
    code = next((c["code"] for c in ds["countries"] if c["id"] == country), "")

    rels: dict[int, dict] = ds["rels"]
    wanted: set[int] = set()
    for r in rels.values():
        wanted.update(r["outer"])
        wanted.update(r["inner"])

    outline = _local_outline(code)
    bbox = None
    if outline is not None:
        w, s, e, n = outline.bounds
        bbox = (w - BBOX_PAD, s - BBOX_PAD, e + BBOX_PAD, n + BBOX_PAD)

    ways = _read_ways(ds["path"], wanted, bbox)
    if not ways:
        raise HTTPException(422, "No boundary geometry could be read from this file.")

    ids: list[int] = []
    geoms: list = []
    for rid, r in rels.items():
        g = _assemble(r, ways)
        if g is not None:
            ids.append(rid)
            geoms.append(g)
    del ways
    if not geoms:
        raise HTTPException(
            422, "No complete administrative boundaries were found near this country."
        )

    country_geom = geoms[ids.index(cid)] if cid in ids else None
    if country_geom is None:
        if outline is None:
            raise HTTPException(422, "That country has no usable outline in this file.")
        country_geom = shapely.buffer(outline, FALLBACK_BUFFER)

    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    shapely.prepare(country_geom)
    keep = np.asarray(shapely.contains(country_geom, shapely.point_on_surface(arr)), dtype=bool)
    if not keep.any():
        raise HTTPException(422, "No boundaries fall inside this country in this file.")

    idx = np.flatnonzero(keep)
    areas = shapely.area(arr[idx])
    lv = np.array([rels[ids[i]]["level"] for i in idx])

    by_level: dict[int, dict] = {}
    for level in sorted(set(lv.tolist())):
        sel = np.flatnonzero(lv == level)
        sel = sel[np.argsort(-areas[sel])]  # big areas first, so small ones are drawn on top
        props = []
        for k in sel:
            rid = ids[idx[k]]
            r = rels[rid]
            props.append(
                {"name": r["name"], "admin_level": level, "osm_id": str(rid), "iso": r["iso"]}
            )
        by_level[level] = {"props": props, "geoms": arr[idx[sel]]}

    minx, miny, maxx, maxy = country_geom.bounds
    return {
        "by_level": by_level,
        "levels": [{"level": level, "count": len(v["props"])} for level, v in by_level.items()],
        "diag": max(math.hypot(maxx - minx, maxy - miny), 1e-9),
        "cache": OrderedDict(),
    }


def _load(ds: dict, country: str) -> dict:
    with _LOCK:
        hit = ds["extracted"].get(country)
        if hit is not None:
            ds["extracted"].move_to_end(country)
            return hit
    with _COMPUTE:
        with _LOCK:
            hit = ds["extracted"].get(country)
        if hit is not None:
            return hit
        try:
            data = _extract(ds, country)
        except HTTPException:
            raise
        except Exception as e:
            log.exception("PBF extraction failed")
            raise HTTPException(500, f"Could not read this country: {_oneline(e)}") from e
        with _LOCK:
            ds["extracted"][country] = data
            while len(ds["extracted"]) > MAX_COUNTRIES:
                ds["extracted"].popitem(last=False)
        return data


# ── step 3: preview / export (from memory) ─────────────────────────────────


def _color(level: int) -> str:
    return _LEVEL_COLORS[level % len(_LEVEL_COLORS)]


def _simplify(arr: np.ndarray, tol: float) -> np.ndarray:
    """Simplify ALL polygons of one level together as a coverage: a shared border is simplified once and stays
    identical on both sides, so no gaps or overlaps appear (simplifying each polygon alone does that).
    """
    if tol <= 0 or len(arr) == 0:
        return arr
    if not hasattr(shapely, "coverage_simplify"):
        raise HTTPException(
            503, "Simplification needs shapely 2.1 or newer. Run: pip install -U shapely"
        )
    try:
        return shapely.coverage_simplify(arr, tol)
    except Exception:  # not a valid coverage (overlapping duplicates): better heavy than wrong
        log.warning("coverage_simplify failed, level left unsimplified", exc_info=True)
        return arr


def _tol(data: dict, pct: int) -> float:
    return 0.0 if pct <= 0 else data["diag"] * (pct / 100) * 0.01


def _preview_features(data: dict, level: int, pct: int) -> list[str]:
    """Serialised GeoJSON features of one level at one simplification; computed once, then cached."""
    key = (level, pct)
    hit = data["cache"].get(key)
    if hit is None:
        lv = data["by_level"][level]
        geoms = _simplify(lv["geoms"], _tol(data, pct))
        gj = shapely.to_geojson(geoms)
        hit = [
            '{"type":"Feature","properties":'
            + json.dumps(
                {**p, "tag": f"ADM{level} · {p['name']}", "color": _color(level), "shade": 0},
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + ',"geometry":'
            + g
            + "}"
            for p, g in zip(lv["props"], gj, strict=True)
        ]
        data["cache"][key] = hit
        while len(data["cache"]) > MAX_PREVIEW_CACHE:
            data["cache"].popitem(last=False)
    return hit


def _preview_body(data: dict, levels: list[int], pct: int) -> str:
    parts = [
        f'"{lv}":[{",".join(_preview_features(data, lv, pct))}]'
        for lv in levels
        if lv in data["by_level"]
    ]
    return '{"pct":%d,"levels":{%s}}' % (pct, ",".join(parts))


def _export(data: dict, country_label: str, levels: list[int], simplify: int) -> tuple[str, str]:
    try:
        from topojson import Topology
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The topojson package is not installed on the server.") from e

    chosen = [lv for lv in levels if lv in data["by_level"]]
    if not chosen:
        raise HTTPException(422, "Pick at least one admin level")
    feats = []
    for lv in chosen:
        item = data["by_level"][lv]
        geoms = _simplify(item["geoms"], _tol(data, simplify))
        for p, g in zip(item["props"], geoms, strict=True):
            feats.append(
                {"type": "Feature", "properties": p, "geometry": json.loads(shapely.to_geojson(g))}
            )
    topology = Topology({"type": "FeatureCollection", "features": feats}, object_name="ADM")
    return topology.to_json(), f"{country_label}-ADM{'-'.join(map(str, chosen))}.topojson"


# ── API ────────────────────────────────────────────────────────────────────


class OpenBody(BaseModel):
    path: str = Field(min_length=1)


class CountryBody(BaseModel):
    token: str
    country: str


class ExtractBody(CountryBody):
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


@router.post("/technical/pbf/country")
async def country_pbf(body: CountryBody):
    """Builds (or returns the cached) boundaries of one country. The slow step; the levels returned are that country's own."""
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    return {"levels": data["levels"], "total": sum(x["count"] for x in data["levels"])}


@router.post("/technical/pbf/preview")
async def preview_pbf(body: ExtractBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    pct = max(PREVIEW_PCT, body.simplify)
    content = await run_in_threadpool(_preview_body, data, _levels(body.levels), pct)
    return Response(content=content, media_type="application/json")


@router.post("/technical/pbf/export")
async def export_pbf(body: ExtractBody):
    ds = _dataset(body.token)
    data = await run_in_threadpool(_load, ds, body.country)
    code = next((c["code"] for c in ds["countries"] if c["id"] == body.country), "")
    content, filename = await run_in_threadpool(
        _export, data, code or body.country, _levels(body.levels), body.simplify
    )
    return Response(
        content=content,
        media_type="application/topojson+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
