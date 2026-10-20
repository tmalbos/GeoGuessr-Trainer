"""stain.py — Technical tool: screenshot of a painted country map -> polygons.

Flow:  POST /technical/stain/upload   -> raw image bytes; kept in memory, returns a token
       POST /technical/stain/analyze  -> {token, country, crop, ...} -> georeferenced stain polygons per colour
Uploads live in memory only (last few); a server restart means loading the image again (410).
Only the stains are returned: the base map is the app's own country map and is never extracted.
The maths is in src/geo/stain_extract.py.
"""

import re
import threading
from collections import OrderedDict
from typing import Literal
from uuid import uuid4

import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.geo.stain_extract import TOL, StainError, analyze, load_image

router = APIRouter()

MAX_UPLOADS = 5
_UPLOADS: OrderedDict[str, dict] = OrderedDict()
_OUTLINES: dict[str, object] = {}
_BORDERS: dict[str, object] = {}
_LOCK = threading.Lock()


def _outline(local, code: str):
    """The country's lon/lat geometry from the local geo layers (cached), or None."""
    if code in _OUTLINES:
        return _OUTLINES[code]
    geom = None
    c = local.countries
    if c is not None and not c.empty:
        sel = c[c["code"] == code]
        if len(sel):
            geom = shapely.union_all(shapely.make_valid(sel.geometry.to_numpy()))
    if geom is None:
        layer = local._admin_layer(code)  # noqa: SLF001
        if layer is not None and len(layer):
            geom = shapely.union_all(shapely.make_valid(layer.geometry.to_numpy()))
    if geom is not None:
        _OUTLINES[code] = geom
    return geom


def _borders(local, code: str):
    """First-level admin borders of the country as lon/lat lines (cached), or None.
    Built by dissolving the finest admin layer by `state`; shared borders appear once.
    """
    if code in _BORDERS:
        return _BORDERS[code]
    out = None
    layer = local._admin_layer(code)  # noqa: SLF001
    if layer is not None and len(layer) and "state" in layer.columns:
        lines = []
        for state, grp in layer.groupby("state"):
            if not state:
                continue
            g = shapely.union_all(shapely.make_valid(grp.geometry.to_numpy()))
            lines.append(shapely.simplify(g, 0.01).boundary)
        if lines:
            out = shapely.union_all(lines)
    _BORDERS[code] = out
    return out


def _prepare(data: bytes) -> dict:
    # "full" = the image the stains are read from; "arr" = small copy used to find the map.
    # "reg" caches the (slow) map registration per country / crop / projection
    full, work = load_image(data)
    return {"full": full, "arr": work, "reg": {}}


@router.post("/technical/stain/upload")
async def stain_upload(request: Request):
    """Raw image bytes in the body."""
    raw = await request.body()
    if not raw:
        raise HTTPException(422, "Empty file")
    try:
        up = await run_in_threadpool(_prepare, raw)
    except StainError as e:
        raise HTTPException(422, str(e)) from e
    token = uuid4().hex
    with _LOCK:
        _UPLOADS[token] = up
        while len(_UPLOADS) > MAX_UPLOADS:
            _UPLOADS.popitem(last=False)
    h, w = up["full"].shape[:2]
    return {"token": token, "width": w, "height": h}


class StainBody(BaseModel):
    token: str
    country: str
    crop: list[float] | None = Field(None, min_length=4, max_length=4)
    projection: Literal["auto", "equirect", "mercator"] = "auto"
    tol: int = Field(TOL, ge=8, le=120)  # shades closer than this are merged into one stain


@router.post("/technical/stain/analyze")
async def stain_analyze(body: StainBody, request: Request):
    up = _UPLOADS.get(body.token)
    if up is None:
        raise HTTPException(410, "That upload expired. Add the image again.")
    if not re.fullmatch(r"[A-Za-z]{2}", body.country):
        raise HTTPException(422, "Invalid country code")
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    code = body.country.upper()
    outline = await run_in_threadpool(_outline, local, code)
    if outline is None:
        raise HTTPException(422, f"No local outline for {code} (see scripts/convert_geo.py)")
    borders = await run_in_threadpool(_borders, local, code)
    try:
        return await run_in_threadpool(
            analyze,
            up["arr"],
            up["full"],
            outline,
            body.crop,
            body.projection,
            body.tol,
            borders,
            up["reg"].setdefault(code, {}),
        )
    except StainError as e:
        raise HTTPException(422, str(e)) from e
