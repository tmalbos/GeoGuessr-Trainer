"""settings.py — Cookie, language, GeoGuessr user id and map source."""

import os

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues import country_projection
from src.explore.country_clues import decks as deck_store
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


class ShortcutBody(BaseModel):
    shortcut: str = Field(min_length=1, max_length=32)


class LevelShortcutsBody(BaseModel):
    shortcuts: list[str] = Field(min_length=5, max_length=5)


class DecksBody(BaseModel):
    decks: list[str]


class ProjectionsBody(BaseModel):
    rules: dict[str, str]  # country code -> projection kind


class StainColorBody(BaseModel):
    color: str = Field(pattern=r"^#(c8283a|2f6fd0|e8c020|7b3fa0|1f6b3a|e0559a|7ac142|e07820)$")


@router.get("/settings")
async def get_settings():
    return {
        "lang": str(prefs.load().get("lang") or "en"),
        "user_id": os.environ.get("GEOGUESSR_USER_ID") or DEFAULT_USER_ID,
        "cookie": bool(load_cookie()),
        "merge_shortcut": str(prefs.load().get("merge_shortcut") or "Shift+G"),
        "stain_color": str(prefs.load().get("stain_color") or "#c8283a"),
        "draw_level_shortcuts": prefs.load().get("draw_level_shortcuts")
        or [f"Alt+{i}" for i in range(1, 6)],
        "decks": deck_store.load(),
        "country_projections": country_projection.load(),
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
    prefs.update(lang=body.lang)
    return {"ok": True}


@router.put("/settings/user")
async def put_user(body: UserBody):
    os.environ["GEOGUESSR_USER_ID"] = body.user_id.strip()
    return {"ok": True}


@router.put("/settings/merge-shortcut")
async def put_merge_shortcut(body: ShortcutBody):
    prefs.update(merge_shortcut=body.shortcut.strip())
    return {"ok": True}


@router.put("/settings/draw-levels")
async def put_draw_levels(body: LevelShortcutsBody):
    prefs.update(draw_level_shortcuts=[s.strip() for s in body.shortcuts])
    return {"ok": True}


@router.put("/settings/stain-color")
async def put_stain_color(body: StainColorBody):
    prefs.update(stain_color=body.color)
    return {"ok": True}


@router.put("/settings/decks")
async def put_decks(body: DecksBody):
    names = list(dict.fromkeys(d for d in map(deck_store.clean, body.decks) if d))
    if not names:
        raise HTTPException(422, "Keep at least one deck")
    prefs.update(decks=names)
    return {"decks": deck_store.load()}


@router.put("/settings/projections")
async def put_projections(body: ProjectionsBody, request: Request):
    """Country -> projection rules. Countries whose rule changed get their saved overlays redrawn."""
    bad = sorted({k for k in body.rules.values() if k not in country_projection.KINDS})
    if bad:
        raise HTTPException(422, f"Unknown projection: {', '.join(bad)}")
    rules = country_projection.clean(body.rules)
    old = country_projection.load()
    changed = sorted(cc for cc in old.keys() | rules.keys() if old.get(cc) != rules.get(cc))
    local = get_ctx(request).local_geo
    if changed and local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Try again in a moment.")
    prefs.update(country_projections=rules or None)
    redrawn = await run_in_threadpool(country_projection.rerender, changed, local, old)
    return {"rules": rules, "rerendered": redrawn}


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
