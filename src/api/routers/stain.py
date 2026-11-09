"""stain.py — Technical tool: screenshot of a painted country map -> polygons.

Flow:  POST /technical/stain/upload   -> raw image bytes; kept in memory, returns a token and where a map
                                         was detected in the image (generic detection, no country needed)
       POST /technical/stain/analyze  -> {token, country, crop, ...} -> georeferenced stain polygons per colour.
                                         Nothing runs when no map was detected (unless the user drew a crop box);
                                         otherwise the detected box biases the search for the map.
Uploads live in memory only (last few); a server restart means loading the image again (410).
All colours inside the map are returned, with the base map flagged `base`; the app's own
country map is drawn underneath, so the UI leaves the base unselected and exports only the stains.
Map pins (pinpoint clues, any colour) are turned into small circles around the pin tip (see src/geo/pinpoint.py)
and flagged `pin`.
The maths is in src/geo/stain_extract.py; the "is there a map?" check is in src/geo/map_detect.py.

Country maps: the outline and the first-level borders come ready-made from LocalGeo (scripts/convert_geo.py
writes them, already simplified together, at the resolution chosen in Settings). Nothing is dissolved or
simplified here. `_outline_full`, `_outline` and `_borders` stay as thin wrappers because clues.py imports them.
"""

import json
import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Literal
from uuid import uuid4

import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.geo.map_detect import detect_map
from src.geo.pinpoint import convert_pins
from src.geo.stain_extract import (
    StainError,
    analyze,
    load_image,
)

router = APIRouter()

FIXED_PARAMS = {"cap": 1.0}  # no longer tunable
PIN_CLIP_DEG = 0.0009  # a pin circle may reach this far past the (simplified) country outline
MIN_STAIN_PCT = (
    0.0  # drop stains whose total area is below this % of the country (no longer in the UI)
)

MAX_UPLOADS = 5
_UPLOADS: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()


def _outline_full(local, code: str):
    """The country outline at the chosen resolution (used to REGISTER the screenshot), or None."""
    return local.outline(code)


def _outline(local, code: str):
    """Same outline, consistent with `_borders`. None when there is no local data."""
    return local.outline(code)


def _borders(local, code: str):
    """First-level borders as lon/lat lines (shared borders appear once), or None."""
    return local.borders(code)


def _use_shared_base(res: dict, local, code: str) -> dict:
    """The base map sent to the UI is the outline simplified together with the borders."""
    outline = _outline(local, code)
    if outline is not None and not outline.is_empty:
        res["base"]["outline"] = json.loads(shapely.to_geojson(outline))
    return res


def _prepare(data: bytes) -> dict:
    # "full" = the image the stains are read from; "arr" = small copy used to find the map.
    # "reg" caches the (slow) map registration per country / crop / projection
    full, work = load_image(data)
    return {"full": full, "arr": work, "reg": {}, "map": detect_map(work)}


class StainParams(BaseModel):
    """Live settings of the stain step (the map registration is reused)."""

    delta_e: float = Field(
        6.67, ge=1.0, le=60.0
    )  # CIEDE2000 distance under which colours are one stain
    expansion_px: int = Field(3, ge=0, le=20)  # pixel layers the stains grow into undefined pixels
    max_classes: int = Field(4, ge=1, le=14)  # stains kept besides the base map


@router.post("/technical/stain/upload")
async def stain_upload(request: Request, path: str | None = None):
    """Raw image bytes in the body; `?path=` reads a file from this machine instead (the app runs locally)."""
    if path is not None:
        p = Path(path.strip().strip('"'))
        if not p.is_file():
            raise HTTPException(404, f"No file at {p}")
        raw = await run_in_threadpool(p.read_bytes)
    else:
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
    return {
        "token": token,
        "width": w,
        "height": h,
        "map": up["map"],
        "params": StainParams().model_dump(),
    }


def _polygons(g) -> shapely.Geometry:
    """Only the polygons of a geometry, as one geometry."""
    parts = [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]
    return shapely.union_all(parts) if parts else shapely.GeometryCollection()


def _mark_pins(res: dict) -> dict:
    """Map pins (pinpoint clues, any colour) become circles around the pin tip, flagged `pin`.

    A stain made only of pins is tagged "Pin n"; a stain that mixes pins with ordinary areas keeps its tag.
    """
    outline = shapely.from_geojson(json.dumps(res["base"]["outline"]))
    clip = shapely.buffer(outline, PIN_CLIP_DEG)
    classes, feats, n = [], [], 0
    for c, f in zip(res["classes"], res["geojson"]["features"], strict=True):
        c.setdefault("pin", False)
        if not c["base"]:
            geom = shapely.from_geojson(json.dumps(f["geometry"]))
            new, pins, rest = convert_pins(geom, outline)
            if pins:
                new = _polygons(shapely.intersection(shapely.make_valid(new), clip))
                if new.is_empty:
                    continue
                f["geometry"] = json.loads(shapely.to_geojson(new))
                if rest == 0:
                    n += 1
                    c["pin"] = True
                    c["tag"] = f["properties"]["tag"] = f"Pin {n}"
        f["properties"]["pin"] = c["pin"]
        classes.append(c)
        feats.append(f)
    res["classes"] = classes
    res["geojson"]["features"] = feats
    return res


class StainBody(BaseModel):
    token: str
    country: str
    crop: list[float] | None = Field(None, min_length=4, max_length=4)
    projection: Literal["auto", "equirect", "plate", "mercator", "lcc", "albers"] = "auto"
    params: StainParams = Field(default_factory=StainParams)
    debug: bool = False  # also return the edge pixels the fit looks at


@router.post("/technical/stain/analyze")
async def stain_analyze(body: StainBody, request: Request):
    up = _UPLOADS.get(body.token)
    if up is None:
        raise HTTPException(410, "That upload expired. Add the image again.")
    det = up["map"]
    if det["found"] is False and body.crop is None:
        # No map in the image: matching a country against a photo would only produce a random placement.
        # A box drawn by the user is an explicit "it is here", so that case still runs.
        raise HTTPException(
            422, "No map was found in this image. Draw a box around it to search anyway."
        )
    hint = (
        det["boxes"][0]["box"] if det["found"] else None
    )  # best box: a rough guess, only biases the search
    if not re.fullmatch(r"[A-Za-z]{2}", body.country):
        raise HTTPException(422, "Invalid country code")
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    code = body.country.upper()
    outline = await run_in_threadpool(_outline_full, local, code)
    if outline is None:
        raise HTTPException(422, f"No local outline for {code} (see scripts/convert_geo.py)")
    borders = await run_in_threadpool(_borders, local, code)
    params = body.params.model_dump()
    try:
        res = await run_in_threadpool(
            analyze,
            up["arr"],
            up["full"],
            outline,
            body.crop,
            body.projection,
            borders,
            up["reg"].setdefault(code, {}),
            hint,
            body.debug,
            {**params, **FIXED_PARAMS},
        )
        res = await run_in_threadpool(_use_shared_base, res, local, code)
        return _mark_pins(res)
    except StainError as e:
        raise HTTPException(422, str(e)) from e
