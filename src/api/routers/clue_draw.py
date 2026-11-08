"""clue_draw.py — Backing for the "Draw" region editor of a regional clue.

GET  /clues/{cc}/draw/units?level=N  -> {levels, units: [{id, name, geometry}]}. Level 0 is the whole country;
                                        1..levels are the local admin layer's levels (state, subregion, city).
POST /clues/{cc}/draw/build          -> GeoJSON FeatureCollection, one colored feature per category name, built from
                                        the drawing's items in order: fills of a subdivision and freeform strokes
                                        (clipped to the country outline). A later item wins over an earlier one.
"""

import json
import math
from typing import Literal

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.api.routers.clues import _cc  # noqa: PLC2701
from src.api.routers.stain import _outline  # noqa: PLC2701
from src.geo.csv_areas import LEVEL_COLUMNS, assign_colors
from src.geo.simplify import _polygonal, simplify_coverage, tolerance  # noqa: PLC2701

router = APIRouter()

_UNITS: dict[tuple[str, int], list[tuple[str, object]]] = {}


def _local(request: Request):
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    return local


def _layer(local, cc: str):
    layer = local._admin_layer(cc)  # noqa: SLF001
    cols = [c for c in LEVEL_COLUMNS if layer is not None and c in layer.columns]
    return layer, cols


def _units(local, cc: str, level: int) -> list[tuple[str, object]]:
    """[(name, geometry)] of the country's units at a level, simplified together. Cached."""
    key = (cc, level)
    if key in _UNITS:
        return _UNITS[key]
    if level == 0:
        outline = _outline(local, cc)
        out = [] if outline is None else [(cc, outline)]
    else:
        layer, all_cols = _layer(local, cc)
        cols = all_cols[:level]
        if level < 0 or len(cols) < level:
            raise HTTPException(422, "That level isn't available for this country.")
        keys = layer[cols].fillna("").astype(str).apply(lambda s: s.str.strip())
        labels = keys[cols[0]]
        for c in cols[1:]:
            labels = labels + " · " + keys[c]
        names, geoms = [], []
        for label, grp in layer.groupby(labels, sort=True):
            g = _polygonal(shapely.union_all(shapely.make_valid(grp.geometry.to_numpy())))
            if g is not None:
                names.append(str(label).strip(" ·") or "—")
                geoms.append(g)
        out = []
        if geoms:
            bounds = shapely.total_bounds(np.array(geoms, dtype=object))
            out = list(zip(names, simplify_coverage(geoms, tolerance(bounds)), strict=True))
    _UNITS[key] = out
    return out


def _payload(local, cc: str, level: int) -> dict:
    units = _units(local, cc, level)
    return {
        "levels": len(_layer(local, cc)[1]),
        "units": [
            {"id": i, "name": n, "geometry": json.loads(shapely.to_geojson(g))}
            for i, (n, g) in enumerate(units)
        ],
    }


@router.get("/clues/{cc}/draw/units")
async def draw_units(cc: str, request: Request, level: int = 0):
    return await run_in_threadpool(_payload, _local(request), _cc(cc), level)


class Item(BaseModel):
    op: Literal["fill", "stroke", "erase"]
    name: str = ""
    level: int = 0
    id: int = 0
    width: float = 0.0  # stroke width, degrees of latitude
    points: list[list[float]] = []  # stroke path, lon/lat


class BuildBody(BaseModel):
    items: list[Item]


def _stroke(points: list[list[float]], width: float):
    k = math.cos(math.radians(points[0][1])) or 1e-6
    pts = [(p[0] * k, p[1]) for p in points]
    line = shapely.Point(pts[0]) if len(pts) == 1 else shapely.LineString(pts)
    g = line.buffer(max(width, 1e-6) / 2, quad_segs=4)
    return shapely.transform(g, lambda c: c / np.array([k, 1.0]))


def _build(local, cc: str, items: list[Item]) -> dict:
    outline = _outline(local, cc)
    if outline is None or outline.is_empty:
        raise HTTPException(422, f"No local outline for {cc}.")
    shapes: dict[str, object] = {}
    for it in items:
        if it.op == "fill":
            units = _units(local, cc, it.level)
            if not 0 <= it.id < len(units):
                continue
            geom = units[it.id][1]
        else:
            pts = [p for p in it.points if len(p) >= 2]
            if not pts:
                continue
            geom = shapely.intersection(_stroke(pts, it.width), outline)
        if geom.is_empty:
            continue
        if it.op == "erase":
            for k in shapes:
                shapes[k] = shapely.difference(shapes[k], geom)
            continue
        name = it.name.strip()
        if not name:
            continue
        for k in shapes:
            if k != name:
                shapes[k] = shapely.difference(shapes[k], geom)
        shapes[name] = shapely.union(shapes[name], geom) if name in shapes else geom
    names, geoms = [], []
    for n, g in shapes.items():
        g = _polygonal(g)
        if g is not None:
            names.append(n)
            geoms.append(g)
    colors = assign_colors(geoms) if geoms else []
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": n, "color": c},
                "geometry": json.loads(shapely.to_geojson(g)),
            }
            for n, c, g in zip(names, colors, geoms, strict=True)
        ],
    }


@router.post("/clues/{cc}/draw/build")
async def draw_build(cc: str, body: BuildBody, request: Request):
    return await run_in_threadpool(_build, _local(request), _cc(cc), body.items)
