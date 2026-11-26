"""clue_draw.py — Backing for the "Draw" region editor of a regional clue.

GET  /clues/{cc}/draw/units?level=N  -> {levels, units: [{id, name, geometry}]}. Level 0 is the whole country;
                                        1..levels are the local admin layer's levels (state, subregion, city).
POST /clues/{cc}/draw/build          -> GeoJSON FeatureCollection, one colored feature per category name, built from
                                        the drawing's items in order: fills of a subdivision and freeform strokes
                                        (clipped to the country outline). A later item wins over an earlier one.

The canvas and the build live in src/explore/country_clues/edit_mode/draw/ (canvas.py, tools.py).
"""

import json
import uuid

import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues.clues import _cc  # noqa: PLC2701
from src.explore.country_clues.edit_mode.draw.canvas import (
    Item,
    _canvas,
    _remember,
    _units,
)
from src.explore.country_clues.edit_mode.draw.tools import _build, _payload
from src.geo.simplify import _polygonal  # noqa: PLC2701

router = APIRouter()


def _local(request: Request):
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    return local


@router.get("/clues/{cc}/draw/units")
async def draw_units(cc: str, request: Request, level: int = 0):
    return await run_in_threadpool(_payload, _local(request), _cc(cc), level)


class BuildBody(BaseModel):
    items: list[Item]
    colored: bool = True  # False = every area keeps the pale land color


class FillBody(BaseModel):
    items: list[Item]
    level: int = 0
    id: int = 0
    point: list[float]  # lon, lat of the click
    erase: bool = False  # Shift + eraser: only report the painted part under the click (None when there is none)


def _fill(local, cc: str, body: FillBody) -> dict:
    """Bucket fill inside one division. Clicked on empty space: the connected empty area around the click.
    Clicked on a painted area: the connected part of that painted area. Never more than the division.
    """
    units = _units(local, cc, body.level)
    if not 0 <= body.id < len(units) or len(body.point) != 2:
        raise HTTPException(422, "Nothing to fill there.")
    unit = units[body.id][1]
    pt = shapely.Point(body.point)
    canvas = _canvas(local, cc, body.items)
    cat = canvas.at(pt)
    if cat is not None:
        area = shapely.intersection(unit, shapely.union_all(canvas.near(unit.bounds, cat)))
    elif body.erase:
        return {"geometry": None}
    else:
        near = canvas.near(unit.bounds)
        area = shapely.difference(unit, shapely.union_all(near)) if len(near) else unit
    area = _polygonal(area)
    parts = [] if area is None else [p for p in shapely.get_parts(area) if p.geom_type == "Polygon"]
    if not parts:
        raise HTTPException(422, "Nothing to fill there.")
    part = next((p for p in parts if p.covers(pt)), None)
    if part is None:
        part = min(parts, key=lambda p: p.distance(pt))
    gid = uuid.uuid4().hex
    _remember(gid, part)  # the browser sends this shape back by gid, not by geometry
    return {"geometry": json.loads(shapely.to_geojson(part)), "gid": gid}


@router.post("/clues/{cc}/draw/fill")
async def draw_fill(cc: str, body: FillBody, request: Request):
    return await run_in_threadpool(_fill, _local(request), _cc(cc), body)


@router.post("/clues/{cc}/draw/build")
async def draw_build(cc: str, body: BuildBody, request: Request):
    return await run_in_threadpool(_build, _local(request), _cc(cc), body.items, body.colored)
