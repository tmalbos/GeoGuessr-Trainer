"""clue_base.py — Country outline + first-level borders, so a regional clue is drawn ON its country.

GET /clues/{cc}/base -> {outline, borders}, both lon/lat GeoJSON (or null when the local geo layers
have nothing for that country). Cached in memory for the life of the server.
"""

import json
import re

import shapely
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.api.routers.stain import _borders, _outline  # noqa: PLC2701

router = APIRouter()

_BASE: dict[str, dict] = {}
_SIMPLIFY_DEG = 0.005


def _geojson(g):
    if g is None or g.is_empty:
        return None
    return json.loads(shapely.to_geojson(shapely.simplify(g, _SIMPLIFY_DEG)))


@router.get("/clues/{cc}/base")
async def clue_base(cc: str, request: Request):
    if not re.fullmatch(r"[A-Za-z]{2}", cc):
        raise HTTPException(422, "Invalid country code")
    cc = cc.upper()
    if cc in _BASE:
        return _BASE[cc]
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    outline = await run_in_threadpool(_outline, local, cc)
    borders = await run_in_threadpool(_borders, local, cc) if outline is not None else None
    _BASE[cc] = {"outline": _geojson(outline), "borders": _geojson(borders)}
    return _BASE[cc]
