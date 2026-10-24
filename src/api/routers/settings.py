"""settings.py — Cookie, language, GeoGuessr user id and map source."""

import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from src.api.deps import get_ctx
from src.geo import map_providers as mp
from src.geoguessr.cookie_extraction import refresh_cookie
from src.geoguessr.cookie_store import load_cookie, save_cookie
from src.i18n.lang import load as load_lang
from src.shared import prefs
from src.sync.user_identity import DEFAULT_USER_ID

router = APIRouter()


class CookieBody(BaseModel):
    cookie: str


class LangBody(BaseModel):
    lang: str


class UserBody(BaseModel):
    user_id: str


class MapBody(BaseModel):
    provider: str = Field(pattern=r"^[a-z0-9_-]{1,32}$")
    google_key: str | None = None  # None = keep the saved one, "" = remove it


@router.get("/settings")
async def get_settings():
    return {
        "lang": os.environ.get("GEOGUESSR_LANG", "en"),
        "user_id": os.environ.get("GEOGUESSR_USER_ID") or DEFAULT_USER_ID,
        "cookie": bool(load_cookie()),
    }


@router.put("/settings/cookie")
async def put_cookie(body: CookieBody):
    if not body.cookie.strip():
        raise HTTPException(422, "Cookie is empty")
    save_cookie(body.cookie.strip())
    return {"ok": True}


@router.post("/settings/cookie/refresh")
async def refresh():
    try:
        return {"ok": bool(await refresh_cookie())}
    except RuntimeError as e:
        raise HTTPException(502, str(e).strip()) from e


@router.put("/settings/lang")
async def put_lang(body: LangBody):
    await load_lang(body.lang)
    os.environ["GEOGUESSR_LANG"] = body.lang
    return {"ok": True}


@router.put("/settings/user")
async def put_user(body: UserBody):
    os.environ["GEOGUESSR_USER_ID"] = body.user_id.strip()
    return {"ok": True}


@router.get("/settings/map")
async def get_map(request: Request):
    """Map source in use. For Google, includes the tile URL template (session + key inside)."""
    ctx = get_ctx(request)
    provider = mp.selected()
    out = {
        "provider": provider,
        "google_key": bool(mp.google_key()),
        "street_view": mp.photos_available(),
        "tile_template": None,
        "error": "",
    }
    if provider == "google":
        await ctx.geoguessr_ready.wait()  # the shared http client exists from here on
        try:
            out["tile_template"] = await mp.google_tile_template(ctx.http_client)
        except httpx.HTTPError:
            out["error"] = "Google didn't accept that key. Check it has the Map Tiles API enabled."
        if out["tile_template"] is None and not out["error"]:
            out["error"] = "No Google API key saved."
    return out


@router.put("/settings/map")
async def put_map(body: MapBody):
    values: dict = {"map_provider": body.provider}
    if body.google_key is not None:
        values["google_key"] = body.google_key.strip() or None
    has_key = bool(values["google_key"]) if "google_key" in values else bool(mp.google_key())
    if body.provider == "google" and not has_key:
        raise HTTPException(422, "Paste a Google API key first.")
    prefs.update(**values)
    return {"ok": True}
