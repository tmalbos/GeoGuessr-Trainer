"""streetview.py — Street View photo for a position and view, from the selected map source.

The source is chosen in Settings (see src/geo/map_providers.py). With a source that has no photos,
every request answers 404 and the UI hides the photo.

Photos are kept in memory for the life of the server only: nothing is written to disk and the
browser is told not to store them either, so a server restart always refetches.

GET /streetview/resolve?url=... follows a shortened Google Maps link (maps.app.goo.gl, goo.gl) and
returns the full URL, which carries the coordinates and view angle.
"""

import logging
from collections import OrderedDict
from typing import Annotated
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from src.api.deps import get_ctx
from src.geo import map_providers as mp

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

_PHOTOS: OrderedDict[tuple, bytes] = OrderedDict()
_PHOTOS_MAX = 200

SHORT_HOSTS = {"maps.app.goo.gl", "goo.gl"}
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


@router.get("/streetview/resolve")
async def streetview_resolve(request: Request, url: str):
    """Full URL behind a short Google Maps link. Only Google's own short hosts are followed."""
    parts = urlparse(url.strip())
    if parts.scheme not in {"http", "https"} or (parts.hostname or "").lower() not in SHORT_HOSTS:
        raise HTTPException(422, "Only short Google Maps links can be resolved.")
    client = get_ctx(request).http_client
    if client is None:
        raise HTTPException(503, "The app is still starting. Try again in a moment.")
    try:
        r = await client.get(
            url.strip(),
            headers={"User-Agent": BROWSER_UA},
            follow_redirects=True,
            timeout=20,
        )
    except httpx.HTTPError as e:
        raise HTTPException(502, "Could not open that Google Maps link.") from e
    final = str(r.url)
    host = urlparse(final).hostname or ""
    if "consent." in host:  # Google's consent page wraps the real URL
        cont = parse_qs(urlparse(final).query).get("continue")
        if cont:
            final = cont[0]
    return {"url": final}


@router.get("/streetview/photo")
async def streetview_photo(
    request: Request,
    lat: Annotated[float, Query(ge=-90, le=90)],
    lng: Annotated[float, Query(ge=-180, le=180)],
    heading: float = 0,
    pitch: float = 0,
    fov: float = 90,
):
    """`fov` is the vertical field of view in degrees (clamped to 10..90)."""
    client = get_ctx(request).http_client
    if client is None:
        raise HTTPException(503, "The app is still starting. Try again in a moment.")
    fov = min(90.0, max(10.0, fov))
    key = (
        mp.selected(),
        round(lat, 6),
        round(lng, 6),
        round(heading % 360, 1),
        round(pitch, 1),
        round(fov),
    )
    data = _PHOTOS.get(key)
    hit = data is not None
    if hit:
        _PHOTOS.move_to_end(key)
    else:
        try:
            data = await mp.fetch_photo(client, lat, lng, heading, pitch, fov)
        except mp.PhotoUnavailableError as e:
            raise HTTPException(
                404, "Street View photos aren't set up. Pick a source in Settings."
            ) from e
        except mp.NoCoverageError as e:
            raise HTTPException(404, "No Street View coverage here.") from e
        except Exception as e:
            logger.exception("Street View photo failed for %s,%s", lat, lng)
            raise HTTPException(502, "Could not fetch the Street View image.") from e
        _PHOTOS[key] = data
        while len(_PHOTOS) > _PHOTOS_MAX:
            _PHOTOS.popitem(last=False)
    return Response(
        data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Cache": "HIT" if hit else "MISS"},
    )
