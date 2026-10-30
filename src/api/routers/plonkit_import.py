"""plonkit_import.py — Import a country's Plonkit guide clue by clue.

POST   /import/plonkit/start          {cc} -> {token, total}   (fetches the guide page once)
GET    /import/plonkit/{token}/{i}    -> the clue prefilled for the editor. Only a window of 2 clues
                                         (the current one + the next) is ever downloaded / kept in memory.
GET    /import/plonkit/{token}/{i}/image -> the raw Plonkit image (used as the region picture of regional clues)
DELETE /import/plonkit/{token}        -> end the import and throw staged files away

A clue is regional when the map detector finds a drawn map in its image, national otherwise.
Tags and text come from scripts/import_plonkit_clues.py (same rules). The source is page#step.
"""

import asyncio
import contextlib
import importlib.util
import logging
import math
import re
import time
import unicodedata
import urllib.parse
from collections import OrderedDict
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.api.routers.clues import _discard, _stage
from src.api.routers.stain import _borders, _outline  # noqa: PLC2701
from src.geo.map_detect import detect_map
from src.geo.stain_extract import StainError, load_image

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

BASE = "https://www.plonkit.net"
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
WINDOW = 2
MAX_SESSIONS = 3
PAUSE_S = 2.0
MAPS_RE = re.compile(
    r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.)?google\.[a-z.]+/maps)\S*", re.IGNORECASE
)

_SESSIONS: OrderedDict[str, dict] = OrderedDict()
_MOD = None


def _script():
    """The existing import script, loaded from its file (its category / text rules are reused as they are)."""
    global _MOD  # noqa: PLW0603
    if _MOD is None:
        path = Path(__file__).parents[3] / "scripts" / "import_plonkit_clues.py"
        spec = importlib.util.spec_from_file_location("import_plonkit_clues", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _find_link(data: dict) -> str | None:
    """First Google Maps link in the tip, looking at its image first."""

    def walk(x):
        if isinstance(x, str):
            m = MAPS_RE.search(x)
            return m.group(0).rstrip(").,;") if m else None
        if isinstance(x, dict):
            for v in x.values():
                if r := walk(v):
                    return r
        if isinstance(x, list):
            for v in x:
                if r := walk(v):
                    return r
        return None

    return walk(data.get("image")) or walk(data)


async def _resolve(client, url: str) -> str:
    """Follow a short maps link to the full URL (it carries the coordinates and view angle)."""
    if not re.search(r"goo\.gl", url):
        return url
    r = await client.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=20)
    final = str(r.url)
    parts = urlparse(final)
    if "consent." in (parts.hostname or ""):
        cont = parse_qs(parts.query).get("continue")
        if cont:
            final = cont[0]
    return final


def _has_map(data: bytes) -> bool:
    try:
        _, work = load_image(data)
    except StainError:
        return False
    return detect_map(work).get("found") is True


def _drop(res: dict | None) -> None:
    img = (res or {}).get("image")
    if img and img.get("token"):
        _discard(img["token"])


def _close(s: dict) -> None:
    for t in s["tasks"].values():
        if t.done() and not t.cancelled():
            _drop(t.result())
        else:
            t.cancel()
    s["tasks"].clear()
    if s.get("warm"):
        s["warm"].cancel()


async def _warm(ctx, cc: str) -> None:
    """Load the country outline / borders now so the stain tool is quick when a regional clue comes."""
    try:
        await ctx.geodata_ready.wait()
        local = ctx.local_geo
        if local is None:
            return
        await run_in_threadpool(_outline, local, cc)
        await run_in_threadpool(_borders, local, cc)
    except Exception:
        logger.exception("Plonkit import: could not warm geo layers")


async def _wait(s: dict, secs: float) -> None:
    """Sleep, and let the UI know how long the user has to wait."""
    s["wait_until"] = time.time() + secs
    await asyncio.sleep(secs)


async def _build(ctx, s: dict, i: int) -> dict:
    tip = s["tips"][i]
    async with s["lock"]:  # one download at a time, with a pause: Plonkit must not see a burst
        try:
            mod = _script()
            client = ctx.http_client
            url = tip["image_url"]
            full = f"{BASE}{url}" if url.startswith("/") else url
            full = urllib.parse.quote(full, safe=":/?#[]@!$&'()*+,;=%~")
            delay = 10.0
            for _ in range(8):  # "too many requests": wait (as long as Plonkit asks) and try again
                r = await client.get(
                    full,
                    headers={"User-Agent": UA, "Referer": s["page_url"]},
                    follow_redirects=True,
                    timeout=30,
                )
                if r.status_code != 429:
                    break
                with contextlib.suppress(ValueError):
                    delay = max(delay, float(r.headers.get("Retry-After", 0)))
                await _wait(s, delay)
                delay *= 2
            r.raise_for_status()
            data = r.content
            regional = await run_in_threadpool(_has_map, data)
            category = mod.category_for(tip["text"], tip["tags"])
            out = {
                "index": i,
                "total": len(s["tips"]),
                "id": tip["id"],
                "step": tip["step"],
                "info": tip["text"],
                "category": category,
                "location": mod.DEFAULT_LOCATION.get(category, "Anywhere"),
                "source": f"{s['page_url']}#{tip['step']}",
                "scope": "reg" if regional else "nat",
                "image": None,
                "has_image": True,
                "street_view_url": None,
                "street_view_error": "",
                "_image": data if regional else None,
                "_media": r.headers.get("content-type", "image/png"),
            }
            if regional:
                if not tip["link"]:
                    out["street_view_error"] = "No Street View link found for this clue."
                else:
                    try:
                        out["street_view_url"] = await _resolve(client, tip["link"])
                    except Exception as e:  # noqa: BLE001
                        out["street_view_error"] = f"Could not open the Street View link: {e}"
            else:
                try:
                    up = await run_in_threadpool(_stage, "image", "plonkit.png", data)
                    out["image"] = {"token": up["token"], "kind": "image"}
                except HTTPException as e:
                    out["street_view_error"] = str(e.detail)
            await _wait(s, PAUSE_S)
            return out
        except Exception as e:
            logger.exception("Plonkit import: clue %s failed", i)
            return {"error": f"Could not load clue {i + 1}: {e}"}


def _task(ctx, s: dict, i: int) -> asyncio.Task:
    t = s["tasks"].get(i)
    if t is None:
        t = s["tasks"][i] = asyncio.create_task(_build(ctx, s, i))
    return t


class StartBody(BaseModel):
    cc: str


@router.post("/import/plonkit/start")
async def plonkit_start(body: StartBody, request: Request):
    ctx = get_ctx(request)
    if not re.fullmatch(r"[A-Za-z]{2}", body.cc):
        raise HTTPException(422, "Invalid country code")
    cc = body.cc.upper()
    await ctx.geoguessr_ready.wait()
    row = await ctx.db_adapter.fetch_one("SELECT name FROM country WHERE code = $1", cc)
    if not row:
        raise HTTPException(404, "That country isn't in the database.")
    name = row["name"]
    mod = _script()
    slugs = list(
        dict.fromkeys([_slug(name), _slug(re.sub(r"^the ", "", name, flags=re.IGNORECASE))])
    )
    guide, page_url = None, ""
    for slug in slugs:
        page_url = f"{BASE}/{slug}"
        try:
            r = await ctx.http_client.get(
                page_url, headers={"User-Agent": UA}, follow_redirects=True, timeout=30
            )
            if r.status_code != 200:
                continue
            g = mod.read_guide(r.text)
        except Exception:  # noqa: BLE001
            continue
        if str(g.get("code") or "").upper() == cc:
            guide = g
            break
    if guide is None:
        raise HTTPException(404, f"Plonkit has no guide for {name}.")

    tips = []
    for step_no, step in enumerate(guide.get("steps") or [], start=1):
        for item in step.get("items") or []:
            if item.get("kind") != "tip":
                continue
            data = item.get("data") or {}
            image_url = (data.get("image") or {}).get("imageUrl")
            text = mod.clean_text(data.get("text") or [])
            if not text or not image_url:
                continue  # nothing to import without text and a picture
            tips.append(
                {
                    "id": item.get("id") or uuid4().hex[:8],
                    "step": step_no,
                    "text": text,
                    "tags": item.get("tags") or [],
                    "image_url": image_url,
                    "link": _find_link(data),
                }
            )
    if not tips:
        raise HTTPException(404, f"The Plonkit guide for {name} has no clues to import.")

    token = uuid4().hex
    s = {
        "cc": cc,
        "page_url": page_url,
        "tips": tips,
        "tasks": {},
        "lock": asyncio.Lock(),
        "wait_until": 0.0,
    }
    s["warm"] = asyncio.create_task(_warm(ctx, cc))
    _SESSIONS[token] = s
    while len(_SESSIONS) > MAX_SESSIONS:
        _close(_SESSIONS.popitem(last=False)[1])
    return {"token": token, "total": len(tips)}


@router.get("/import/plonkit/{token}/wait")
async def plonkit_wait(token: str):
    """Seconds left before the next clue can be downloaded."""
    s = _SESSIONS.get(token)
    if s is None:
        raise HTTPException(410, "That import expired. Start it again.")
    return {"seconds": max(0, math.ceil(s["wait_until"] - time.time()))}


@router.get("/import/plonkit/{token}/{index}")
async def plonkit_clue(token: str, index: int, request: Request):
    ctx = get_ctx(request)
    s = _SESSIONS.get(token)
    if s is None:
        raise HTTPException(410, "That import expired. Start it again.")
    n = len(s["tips"])
    if not 0 <= index < n:
        raise HTTPException(404, "No such clue")
    await ctx.geoguessr_ready.wait()
    for j in [j for j in s["tasks"] if j < index]:  # the ones behind us are done with
        t = s["tasks"].pop(j)
        if t.done() and not t.cancelled():
            _drop(t.result())
        else:
            t.cancel()
    cur = _task(ctx, s, index)
    for j in range(index + 1, min(index + WINDOW, n)):  # next ones, in the background
        _task(ctx, s, j)
    res = await cur
    if "error" in res:
        s["tasks"].pop(index, None)
        raise HTTPException(502, res["error"])
    return {k: v for k, v in res.items() if not k.startswith("_")}


@router.get("/import/plonkit/{token}/{index}/image")
async def plonkit_image(token: str, index: int):
    s = _SESSIONS.get(token)
    t = s["tasks"].get(index) if s else None
    if t is None or not t.done() or t.cancelled():
        raise HTTPException(404, "Image not available")
    res = t.result()
    if not res.get("_image"):
        raise HTTPException(404, "Image not available")
    return Response(res["_image"], media_type=res.get("_media") or "image/png")


@router.delete("/import/plonkit/{token}")
async def plonkit_stop(token: str):
    s = _SESSIONS.pop(token, None)
    if s:
        _close(s)
    return {"ok": True}
