"""streetview.py — Street View photo for a position and view (keyless, see streetview_photo.py)."""

import logging
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from src.api.deps import get_ctx
from src.geoguessr.streetview_photo import LatLng, NoStreetViewCoverageError, retrieve_photo

logger = logging.getLogger("uvicorn.error")
router = APIRouter()


@router.get("/streetview/photo")
async def streetview_photo(
    request: Request,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    heading: float = 0,
    pitch: float = 0,
    fov: float = 90,
):
    """`fov` is the horizontal field of view in degrees (clamped to 10..90)."""
    client = get_ctx(request).http_client
    if client is None:
        raise HTTPException(503, "The app is still starting. Try again in a moment.")
    zoom = (90 - min(90.0, max(10.0, fov))) / 80
    try:
        data = await retrieve_photo(client, LatLng(lat, lng), heading, pitch, zoom)
    except NoStreetViewCoverageError as e:
        raise HTTPException(404, "No Street View coverage here.") from e
    except Exception as e:
        logger.exception("Street View photo failed for %s,%s", lat, lng)
        raise HTTPException(502, "Could not fetch the Street View image.") from e
    return Response(
        data, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=86400"}
    )
