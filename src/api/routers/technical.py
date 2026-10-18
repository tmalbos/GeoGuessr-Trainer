"""technical.py — Technical tools. First one: polygonize tagged points from a mapmaking.app export.

Flow:  POST /technical/mapmaking   -> raw JSON file in the body; parsed and kept in memory, returns tags + points
       POST /technical/polygonize  -> {token, tags, method, ...} -> one GeoJSON feature per selected tag
Datasets live in memory only (last few uploads); a server restart means loading the file again (410).
Distances are approximated in degrees (1 deg ~ 111 km), fine for a first pass but loose near the poles.
"""

import colorsys
import json
import threading
import time
from collections import OrderedDict
from typing import Literal
from uuid import uuid4

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx

router = APIRouter()

KM_PER_DEG = 111.32
MAX_DATASETS = 3
MAX_CACHE = 300

_DATASETS: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()
_LAND: dict = {"src": None, "geom": None}


# ── parsing ────────────────────────────────────────────────────────────────


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(v))) for v in rgb[:3]))


def _fallback_color(i: int) -> str:
    r, g, b = colorsys.hsv_to_rgb((i * 0.618034) % 1, 0.65, 0.95)
    return _hex((r * 255, g * 255, b * 255))


def _parse(raw: bytes) -> dict:
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise HTTPException(422, "That file isn't valid JSON") from e
    coords = data.get("customCoordinates") if isinstance(data, dict) else None
    if not isinstance(coords, list):
        raise HTTPException(422, "Not a mapmaking.app export: customCoordinates is missing")
    meta = ((data.get("extra") or {}).get("tags")) or {}

    index: dict[str, int] = {}
    points: list[list] = []
    for c in coords:
        if not isinstance(c, dict):
            continue
        lat, lng = c.get("lat"), c.get("lng")
        if not isinstance(lat, int | float) or not isinstance(lng, int | float):
            continue
        ids = [
            index.setdefault(t, len(index))
            for t in (c.get("extra") or {}).get("tags") or []
            if isinstance(t, str)
        ]
        if ids:
            points.append([round(lat, 5), round(lng, 5), ids])
    if not points:
        raise HTTPException(422, "No tagged locations found in this file")

    names = list(index)
    xy_lists: list[list] = [[] for _ in names]
    for lat, lng, ids in points:
        for i in ids:
            xy_lists[i].append((lng, lat))
    xy = {n: np.unique(np.array(xy_lists[i], dtype=float), axis=0) for i, n in enumerate(names)}

    tags = []
    for i, n in enumerate(names):
        col = (meta.get(n) or {}).get("color")
        tags.append(
            {
                "name": n,
                "count": len(xy_lists[i]),
                "color": _hex(col)
                if isinstance(col, list) and len(col) >= 3
                else _fallback_color(i),
            }
        )
    order = sorted(range(len(tags)), key=lambda i: -tags[i]["count"])
    remap = {old: new for new, old in enumerate(order)}
    tags = [tags[i] for i in order]
    points = [[la, ln, [remap[i] for i in ids]] for la, ln, ids in points]
    return {
        "name": str(data.get("name") or "Map"),
        "total": len(coords),
        "tags": tags,
        "points": points,
        "xy": xy,
        "cache": {},
    }


@router.post("/technical/mapmaking")
async def load_mapmaking(request: Request):
    """Raw file bytes in the body (same style as the clue uploads)."""
    raw = await request.body()
    if not raw:
        raise HTTPException(422, "Empty file")
    ds = await run_in_threadpool(_parse, raw)
    token = uuid4().hex
    with _LOCK:
        _DATASETS[token] = ds
        while len(_DATASETS) > MAX_DATASETS:
            _DATASETS.popitem(last=False)
    return {k: ds[k] for k in ("name", "total", "tags", "points")} | {"token": token}


# ── polygonizing ───────────────────────────────────────────────────────────


class PolyBody(BaseModel):
    token: str
    tags: list[str]
    method: Literal["blob", "hull"] = "blob"
    link_km: float = Field(100, ge=1, le=3000)  # points closer than this belong to the same area
    pad_km: float = Field(10, ge=0, le=500)  # minimum radius painted around every point
    ratio: float = Field(0.3, ge=0, le=1)  # hull only: 0 = tightest, 1 = convex
    clip: bool = False  # cut to the countries known by the local geo layers


def _shape(xy: np.ndarray, p: PolyBody):
    link = p.link_km / KM_PER_DEG
    pad = max(p.pad_km / KM_PER_DEG, 1e-4)
    pts = shapely.points(xy)

    if p.method == "blob":
        # Grow by pad + link/2, shrink by link/2: gaps up to ~link get closed, nothing else moves.
        g = shapely.union_all(shapely.buffer(pts, pad + link / 2, quad_segs=4))
        return shapely.buffer(g, -link / 2, quad_segs=4)

    # hull: group points into areas, then wrap each group in a concave hull
    blobs = shapely.get_parts(shapely.union_all(shapely.buffer(pts, link / 2, quad_segs=4)))
    pi, bi = shapely.STRtree(blobs).query(pts, predicate="within")
    if not len(pi):
        return None
    order = np.argsort(bi, kind="stable")
    pi, bi = pi[order], bi[order]
    out = []
    for grp in np.split(pi, np.flatnonzero(np.diff(bi)) + 1):
        mp = shapely.multipoints(xy[grp])
        try:
            hull = shapely.concave_hull(mp, ratio=p.ratio)
        except Exception:  # noqa: BLE001
            hull = shapely.convex_hull(mp)
        out.append(shapely.buffer(hull, pad, quad_segs=4))
    return shapely.union_all(out)


def _clip(g, land):
    try:
        return shapely.intersection(g, shapely.clip_by_rect(land, *g.bounds))
    except Exception:  # noqa: BLE001
        return g


def _polys(g):
    """Only the polygonal part of a geometry, as one Polygon / MultiPolygon (or None)."""
    if g is None:
        return None
    parts = [q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and not q.is_empty]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else shapely.multipolygons(parts)


def _land(local):
    if local.countries is None or local.countries.empty:
        return None
    if _LAND["src"] is not local:
        _LAND["geom"] = shapely.union_all(shapely.make_valid(local.countries.geometry.to_numpy()))
        _LAND["src"] = local
    return _LAND["geom"]


def _run(ds: dict, p: PolyBody, land) -> tuple[list[dict], list[dict]]:
    colors = {t["name"]: t["color"] for t in ds["tags"]}
    tol = min(0.05, max(0.0003, p.link_km / KM_PER_DEG * 0.02))
    feats, stats = [], []
    for tag in p.tags:
        key = (tag, p.method, p.link_km, p.pad_km, round(p.ratio, 2), land is not None)
        hit = ds["cache"].get(key)
        if hit is None:
            t0 = time.perf_counter()
            xy = ds["xy"][tag]
            g = _shape(xy, p)
            if g is not None and land is not None:
                g = _clip(g, land)
            if g is not None:
                g = _polys(shapely.simplify(g, tol, preserve_topology=True))
            geom = json.loads(shapely.to_geojson(g)) if g is not None else None
            hit = (
                geom,
                0 if g is None else int(shapely.get_num_geometries(g)),
                len(xy),
                round((time.perf_counter() - t0) * 1000),
            )
            while len(ds["cache"]) >= MAX_CACHE:
                ds["cache"].pop(next(iter(ds["cache"])))
            ds["cache"][key] = hit
        geom, n_polys, n_pts, ms = hit
        stats.append({"tag": tag, "points": n_pts, "polygons": n_polys, "ms": ms})
        if geom:
            feats.append(
                {
                    "type": "Feature",
                    "properties": {"tag": tag, "color": colors[tag], "points": n_pts},
                    "geometry": geom,
                }
            )
    return feats, stats


@router.post("/technical/polygonize")
async def polygonize(body: PolyBody, request: Request):
    ds = _DATASETS.get(body.token)
    if ds is None:
        raise HTTPException(410, "That upload expired. Add the file again.")
    missing = [t for t in body.tags if t not in ds["xy"]]
    if missing:
        raise HTTPException(422, f"Unknown tags: {', '.join(missing[:3])}")
    land, warning = None, ""
    if body.clip:
        local = get_ctx(request).local_geo
        land = await run_in_threadpool(_land, local) if local is not None else None
        if land is None:
            warning = "Geo layers aren't loaded, so nothing was clipped."
    feats, stats = await run_in_threadpool(_run, ds, body, land)
    return {
        "geojson": {"type": "FeatureCollection", "features": feats},
        "stats": stats,
        "warning": warning,
    }
