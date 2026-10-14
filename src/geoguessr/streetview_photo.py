"""streetview_photo.py — A flat Street View photo for a position, with no API key and no Maps SDK.

    lat/lng --(A)--> zoom-17 tile --(B)--> nearest panoId --(C)--> JPEG

A: web-mercator tile math.
B: https://www.google.com/maps/photometa/ac/v1  (undocumented, keyless): every pano dot in a tile.
C: https://streetviewpixels-pa.googleapis.com/v1/thumbnail (undocumented, keyless): a perspective crop.

Both endpoints are undocumented internals. They are isolated in `_fetch_pano_dots` and
`_fetch_thumbnail` so a paid Static API fallback could replace them later.
"""

import asyncio
import json
import math
import re
from collections import OrderedDict
from dataclasses import dataclass

import httpx

TILE_ZOOM = 17
TILE_SIZE = 256
SEARCH_RADIUS_M = 50
OUT_W, OUT_H = 640, 360  # 16:9, like the street view window
TILE_CACHE_MAX = 512
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.google.com/"}
TIMEOUT_S = 15


@dataclass(frozen=True)
class LatLng:
    lat: float
    lng: float


class NoStreetViewCoverageError(Exception):
    def __init__(self, position: LatLng) -> None:
        super().__init__("No Street View coverage at position")
        self.position = position


_clamp01 = lambda v: min(1.0, max(0.0, v))  # noqa: E731
_clamp_pitch = lambda v: min(90.0, max(-90.0, v))  # noqa: E731
_norm_heading = lambda h: ((h % 360) + 360) % 360  # noqa: E731


# ── A: lat/lng -> tile ──────────────────────────────────────────────────────
def lat_lng_to_tile(lat: float, lng: float) -> tuple[int, int]:
    n = min(max(math.sin(math.radians(lat)), -0.9999), 0.9999)
    wx = TILE_SIZE * (0.5 + lng / 360)
    wy = TILE_SIZE * (0.5 - math.log((1 + n) / (1 - n)) / (4 * math.pi))
    return math.floor(wx * 2**TILE_ZOOM / TILE_SIZE), math.floor(wy * 2**TILE_ZOOM / TILE_SIZE)


def _dist_m(a: LatLng, lat: float, lng: float) -> float:
    r, d = 6371000.0, math.pi / 180
    dlat, dlng = (lat - a.lat) * d, (lng - a.lng) * d
    s = math.sin(dlat / 2) ** 2 + math.cos(a.lat * d) * math.cos(lat * d) * math.sin(dlng / 2) ** 2
    return 2 * r * math.asin(math.sqrt(s))


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET with a short linear backoff on 429 / 503."""
    res = None
    for attempt in range(3):
        res = await client.get(url, headers=HEADERS, timeout=TIMEOUT_S)
        if res.status_code not in {429, 503}:
            return res
        await asyncio.sleep(0.5 * (attempt + 1))
    return res


# ── B: tile -> pano dots -> nearest panoId ──────────────────────────────────
_TILES: OrderedDict[tuple[int, int], list[tuple[float, float, str]]] = OrderedDict()


async def _fetch_pano_dots(
    client: httpx.AsyncClient, x: int, y: int
) -> list[tuple[float, float, str]]:
    if (x, y) in _TILES:
        _TILES.move_to_end((x, y))
        return _TILES[x, y]
    pb = f"!1m2!1sapiv3!5sen!6m3!1i{x}!2i{y}!3i{TILE_ZOOM}"
    res = await _get(client, f"https://www.google.com/maps/photometa/ac/v1?pb={pb}")
    if res.status_code != 200:
        return []
    try:
        data = json.loads(re.sub(r"^\)\]\}'\n?", "", res.text))
    except ValueError:
        return []
    dots = []
    try:
        entries = data[1][1] or []
    except (IndexError, KeyError, TypeError):
        entries = []
    for entry in entries:
        try:
            info = entry[0]
            pos = info[2][0]
            pano_id = info[0][1]
            if isinstance(pos[2], int | float) and isinstance(pos[3], int | float) and pano_id:
                dots.append((float(pos[2]), float(pos[3]), pano_id))
        except (IndexError, KeyError, TypeError):
            continue
    _TILES[x, y] = dots
    while len(_TILES) > TILE_CACHE_MAX:
        _TILES.popitem(last=False)
    return dots


async def resolve_pano_id(
    client: httpx.AsyncClient, pos: LatLng, radius: float = SEARCH_RADIUS_M
) -> str | None:
    tx, ty = lat_lng_to_tile(pos.lat, pos.lng)
    tiles = [(tx, ty)] + [(tx + dx, ty + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1) if dx or dy]
    best: tuple[float, str] | None = None
    for x, y in tiles:  # centre tile first; neighbours only as a fallback
        for lat, lng, pano_id in await _fetch_pano_dots(client, x, y):
            d = _dist_m(pos, lat, lng)
            if d < radius and (best is None or d < best[0]):
                best = (d, pano_id)
        if best:
            break
    return best[1] if best else None


# ── C: panoId + orientation -> JPEG ─────────────────────────────────────────
async def _fetch_thumbnail(
    client: httpx.AsyncClient, pano_id: str, heading: float, pitch: float, fov: float
) -> bytes:
    failures = []
    for cb_client in ("maps_sv.share", "maps_sv.tactile"):
        params = httpx.QueryParams(
            {
                "panoid": pano_id,
                "cb_client": cb_client,
                "w": OUT_W,
                "h": OUT_H,
                "yaw": heading,
                "pitch": pitch,
                "thumbfov": round(fov),  # the endpoint only accepts an integer
            },
        )
        res = await _get(
            client, f"https://streetviewpixels-pa.googleapis.com/v1/thumbnail?{params}"
        )
        if res.status_code == 200 and res.content:
            return res.content
        failures.append(f"{cb_client}: HTTP {res.status_code} {res.text[:200]!r}")
    msg = f"Street View thumbnail failed for pano {pano_id}: " + " | ".join(failures)
    raise RuntimeError(msg)


async def retrieve_photo(
    client: httpx.AsyncClient, position: LatLng, heading_deg: float, pitch_deg: float, zoom: float
) -> bytes:
    """JPEG bytes of the latest pano near `position`. zoom 0..1 = fov 90..10 degrees."""
    pano_id = await resolve_pano_id(client, position)
    if not pano_id:
        raise NoStreetViewCoverageError(position)
    fov = 90 - _clamp01(zoom) * 80
    return await _fetch_thumbnail(
        client, pano_id, _norm_heading(heading_deg), _clamp_pitch(pitch_deg), fov
    )
