"""settings.py — Cookie, language and GeoGuessr user id."""

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from src.geoguessr.cookie_extraction import refresh_cookie
from src.geoguessr.cookie_store import load_cookie, save_cookie
from src.i18n.lang import load as load_lang
from src.sync.user_identity import DEFAULT_USER_ID

router = APIRouter()


class CookieBody(BaseModel):
    cookie: str


class LangBody(BaseModel):
    lang: str


class UserBody(BaseModel):
    user_id: str


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
