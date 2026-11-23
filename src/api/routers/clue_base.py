"""clue_base.py — Country outline + first-level borders, so a regional clue is drawn ON its country.

GET /clues/{cc}/base -> {outline, borders}, both lon/lat GeoJSON (or null when the local geo layers
have nothing for that country). Cached in memory for the life of the server.

Both come from stain._outline / stain._borders, which are already simplified TOGETHER (src/geo/simplify.py,
resolution based). Nothing is simplified again here: a second, fixed simplification is what made small
countries look heavily simplified and let the borders drift away from the outline.
"""

import re

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues.clues import base

router = APIRouter()


@router.get("/clues/{cc}/base")
async def clue_base(cc: str, request: Request):
    if not re.fullmatch(r"[A-Za-z]{2}", cc):
        raise HTTPException(422, "Invalid country code")
    local = get_ctx(request).local_geo
    return await run_in_threadpool(base, cc, local)
