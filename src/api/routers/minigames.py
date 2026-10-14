"""minigames.py — real-map windows for the minigames.

GET /minigames/countries            window sizes + indexed countries (+ ones still waiting for an index)
GET /minigames/place?size_km=6      one random valid window as a binary payload (see src/minigames/window.py)
"""

import asyncio
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from src.minigames.index import list_countries
from src.minigames.service import NoPlace, generate_place

router = APIRouter(prefix="/minigames", tags=["minigames"])


@router.get("/countries")
async def countries() -> dict:
    return await asyncio.to_thread(list_countries)


@router.get("/place")
async def place(size_km: Annotated[int, Query()], country: str | None = None) -> Response:
    try:
        payload = await asyncio.to_thread(generate_place, size_km, country)
    except NoPlace as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return Response(content=payload, media_type="application/octet-stream")
