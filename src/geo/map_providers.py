"""map_providers.py — Where map tiles and Street View photos come from.

Built in:
    osm     OpenStreetMap tiles (no key). No Street View photos.
    google  Google's official APIs with the user's own key (Map Tiles API + Street View Static API).

Anything else is a local plug-in: a module in src/private/ that defines

    PROVIDER = {"id": "...", "label": "...", "photo": async (client, lat, lng, heading, pitch, fov) -> bytes}

`fov` is the vertical field of view in degrees. A plug-in raises NoCoverageError when there is no
imagery. src/private/ does not exist in the public repo; then there are simply no plug-ins.
"""

import importlib
import importlib.util
import logging
import math
import pkgutil
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from src.shared import prefs

logger = logging.getLogger("uvicorn.error")

DEFAULT_PROVIDER = "osm"
GOOGLE_SESSION_URL = "https://tile.googleapis.com/v1/createSession"
GOOGLE_TILE_URL = "https://tile.googleapis.com/v1/2dtiles/{z}/{x}/{y}"
GOOGLE_STREETVIEW_URL = "https://maps.googleapis.com/maps/api/streetview"
PHOTO_ASPECT = 16 / 9  # keep equal to ASPECT in frontend StreetPhoto.jsx

PhotoFetch = Callable[[httpx.AsyncClient, float, float, float, float, float], Awaitable[bytes]]


class NoCoverageError(Exception):
    """The provider has no imagery at that spot."""


class PhotoUnavailableError(Exception):
    """The selected provider can't make photos (no key, or it doesn't do photos)."""


@dataclass(frozen=True)
class LocalProvider:
    id: str
    label: str
    photo: PhotoFetch


# ── local plug-ins ─────────────────────────────────────────────────────────

_LOCAL: dict[str, LocalProvider] | None = None


def _discover() -> dict[str, LocalProvider]:
    found: dict[str, LocalProvider] = {}
    try:
        spec = importlib.util.find_spec("src.private")
    except (ImportError, ValueError):
        return found
    if spec is None or not spec.submodule_search_locations:
        return found
    for info in pkgutil.iter_modules(spec.submodule_search_locations):
        try:
            mod = importlib.import_module(f"src.private.{info.name}")
            p = getattr(mod, "PROVIDER", None)
            if p:
                found[p["id"]] = LocalProvider(p["id"], p.get("label", p["id"]), p["photo"])
        except Exception:
            logger.exception("Could not load local provider %s", info.name)
    return found


def local_providers() -> dict[str, LocalProvider]:
    global _LOCAL  # noqa: PLW0603
    if _LOCAL is None:
        _LOCAL = _discover()
    return _LOCAL


# ── selection ──────────────────────────────────────────────────────────────


def selected() -> str:
    return str(prefs.load().get("map_provider") or DEFAULT_PROVIDER)


def google_key() -> str:
    return str(prefs.load().get("google_key") or "").strip()


def photos_available() -> bool:
    choice = selected()
    if choice == "google":
        return bool(google_key())
    return choice in local_providers()


# ── Google (official, user's key) ──────────────────────────────────────────

_session = {"key": "", "id": "", "expires": 0.0}


async def google_tile_template(client: httpx.AsyncClient) -> str | None:
    """Tile URL template with {z}/{x}/{y} left in, or None when there is no key.

    Raises httpx.HTTPError when Google refuses the key.
    """
    key = google_key()
    if not key:
        return None
    now = time.time()
    if _session["key"] != key or _session["expires"] - 3600 < now:
        r = await client.post(
            f"{GOOGLE_SESSION_URL}?key={key}",
            json={"mapType": "roadmap", "language": "en-US", "region": "US"},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        _session.update(
            key=key,
            id=data["session"],
            expires=float(data.get("expiry") or now + 7 * 86400),
        )
    return f"{GOOGLE_TILE_URL}?session={_session['id']}&key={key}"


async def _google_photo(
    client: httpx.AsyncClient, lat: float, lng: float, heading: float, pitch: float, fov: float
) -> bytes:
    key = google_key()
    if not key:
        raise PhotoUnavailableError
    # The Static API takes the HORIZONTAL fov; the app's views carry the vertical one.
    hfov = math.degrees(2 * math.atan(math.tan(math.radians(fov) / 2) * PHOTO_ASPECT))
    r = await client.get(
        GOOGLE_STREETVIEW_URL,
        params={
            "size": "640x360",
            "location": f"{lat},{lng}",
            "heading": heading,
            "pitch": pitch,
            "fov": round(min(120, max(10, hfov))),
            "source": "outdoor",
            "return_error_code": "true",
            "key": key,
        },
        timeout=15,
    )
    if r.status_code == 404:
        raise NoCoverageError
    r.raise_for_status()
    return r.content


async def fetch_photo(
    client: httpx.AsyncClient, lat: float, lng: float, heading: float, pitch: float, fov: float
) -> bytes:
    """A Street View photo from the selected provider."""
    choice = selected()
    if choice == "google":
        return await _google_photo(client, lat, lng, heading, pitch, fov)
    local = local_providers().get(choice)
    if local is None:
        raise PhotoUnavailableError
    return await local.photo(client, lat, lng, heading, pitch, fov)
