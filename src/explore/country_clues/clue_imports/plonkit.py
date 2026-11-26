"""plonkit.py — import a country's Plonkit guide clue by clue."""

import asyncio
import contextlib
import io
import json
import logging
import math
import re
import time
import unicodedata
import urllib.parse
from collections import OrderedDict
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from PIL import Image
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues.clue_imports.clue_titles import (
    DEFAULT_LOCATION,
    category_for,
    classify_full,
)
from src.explore.country_clues.regional_stain_areas import _borders, _outline

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

BASE = "https://www.plonkit.net"
SPACES_RE = re.compile(r"[ \t]+")
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MAX_SESSIONS = 3
PAUSE_S = 3.0
MAX_WAIT_S = 60.0  # longer than this to wait after a 429: stop retrying and tell the user
MAPS_RE = re.compile(
    r"https?://(?:maps\.app\.goo\.gl|goo\.gl/maps|(?:www\.)?google\.[a-z.]+/maps)\S*", re.IGNORECASE
)
TITLE_KEYS = ("title", "name", "label", "heading", "header")

_SESSIONS: OrderedDict[str, dict] = OrderedDict()


def read_guide(html: str) -> dict:
    """Pull the embedded guide JSON out of a Plonkit page."""
    match = re.search(
        r'<script id="__PRELOADED_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        msg = "This page has no __PRELOADED_DATA__ block (is it a Plonkit guide?)"
        raise ValueError(msg)
    payload = json.loads(match.group(1))
    guide = payload.get("data", {}).get("public")
    if not guide:
        msg = "The page did not contain a public guide"
        raise ValueError(msg)
    return guide


def national_step(guide: dict) -> dict:
    """Return the first step, which is Plonkit's national 'Identifying …' section."""
    steps = guide.get("steps") or []
    if not steps:
        msg = "This guide has no steps"
        raise ValueError(msg)
    first = steps[0]
    title = (first.get("title") or "").lower()
    if "identifying" not in title:
        msg = f"First step is {first.get('title')!r}, not an 'Identifying ...' section"
        raise ValueError(msg)
    return first


def clean_text(paragraphs: list[str]) -> str:
    """Join Plonkit's paragraphs with blank lines and repair its markdown quirks."""
    out: list[str] = []
    for raw in paragraphs:
        text = raw.strip().replace("\u00a0", " ")
        text = SPACES_RE.sub(" ", text)
        # Relative guide links (e.g. an infographic) stay usable in the clue card.
        text = re.sub(r"\]\((/(?!/)[^\s)]+)\)", rf"]({BASE}\1)", text)
        # Plonkit sometimes leaves a space before the closing ** ("**Town signs **are").
        text = re.sub(r"\*\*([^*\n]+?)\s+\*\*(?=\w)", r"**\1** ", text)
        text = re.sub(r"\*\*([^*\n]+?)\s+\*\*", r"**\1**", text)
        if text:
            out.append(text)
    return "\n\n".join(out)


def _slug(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _title_of(node: dict) -> str | None:
    """The readable title of a step or divider, or None when it carries none."""
    for key in TITLE_KEYS:
        v = node.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, list):
            text = " ".join(x.strip() for x in v if isinstance(x, str) and x.strip())
            if text:
                return text
    return None


def _section_title(step: dict, number: int) -> str:
    """The name of a guide step, or "Section N" when the step carries no readable title."""
    return _title_of(step) or f"Section {number}"


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


def _close(s: dict) -> None:
    for t in s["tasks"].values():
        t.cancel()
    s["tasks"].clear()
    if s.get("warm"):
        s["warm"].cancel()


def _log_blocked(where: str, r) -> None:
    """Log what Plonkit answered instead of 200: tells a rate limit apart from a Cloudflare challenge."""
    keep = (
        "retry-after",
        "cf-mitigated",
        "server",
        "cf-ray",
        "content-type",
        "x-ratelimit-remaining",
        "x-ratelimit-reset",
    )
    headers = {k: v for k, v in r.headers.items() if k.lower() in keep}
    logger.warning(
        "Plonkit import: %s answered %s, headers=%s, body=%r",
        where,
        r.status_code,
        headers,
        r.text[:300],
    )


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


def _image_size(data: bytes) -> tuple[int, int] | None:
    """(width, height) from the first bytes of an image, or None while there are too few."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            return im.size
    except Exception:  # noqa: BLE001
        return None


async def _build(ctx, s: dict, i: int) -> dict:
    tip = s["tips"][i]
    meta = s["meta"][i]
    async with s["lock"]:  # one download at a time, with a pause: Plonkit must not see a burst
        try:
            client = ctx.http_client
            url = tip["image_url"]
            full = f"{BASE}{url}" if url.startswith("/") else url
            full = urllib.parse.quote(full, safe=":/?#[]@!$&'()*+,;=%~")
            delay = 10.0
            for _ in range(8):  # "too many requests": wait (as long as Plonkit asks) and try again
                gap = s["last"] + PAUSE_S - time.monotonic()
                if gap > 0:  # never two requests to Plonkit closer than PAUSE_S
                    await _wait(s, gap)
                async with client.stream(
                    "GET",
                    full,
                    headers={"User-Agent": UA, "Referer": s["page_url"]},
                    follow_redirects=True,
                    timeout=30,
                ) as r:
                    s["last"] = time.monotonic()
                    if r.status_code >= 400:
                        await r.aread()
                        _log_blocked(full, r)
                    if r.status_code != 429:
                        r.raise_for_status()
                        meta["media"] = r.headers.get("content-type", "image/png")
                        body = bytearray()
                        async for chunk in r.aiter_bytes():
                            body += chunk
                            if meta["size"] is None and (size := _image_size(bytes(body))):
                                meta["size"] = (
                                    size  # the UI can size its box before the rest arrives
                                )
                                meta["ev"].set()
                        return {"data": bytes(body), "media": meta["media"]}
                    asked = 0.0
                    with contextlib.suppress(ValueError):
                        asked = float(r.headers.get("Retry-After", 0))
                delay = max(delay, asked)
                if delay > MAX_WAIT_S:
                    mins = max(1, round(delay / 60))
                    return {
                        "error": f"Plonkit is limiting requests. Try again in about {mins} min."
                    }
                await _wait(s, delay)
                delay *= 2
            msg = "Plonkit kept answering 'too many requests'."
            raise RuntimeError(msg)  # noqa: TRY301
        except Exception as e:
            logger.exception("Plonkit import: clue %s failed", i)
            return {"error": f"Could not load clue {i + 1}: {e}"}
        finally:
            meta["ev"].set()


def _task(ctx, s: dict, i: int) -> asyncio.Task:
    t = s["tasks"].get(i)
    if t is None or t.cancelled():
        s["meta"][i] = {"size": None, "media": "image/png", "ev": asyncio.Event()}
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
                if r.status_code != 404:
                    _log_blocked(page_url, r)
                continue
            g = read_guide(r.text)
        except Exception:  # noqa: BLE001
            continue
        if str(g.get("code") or "").upper() == cc:
            guide = g
            break
    if guide is None:
        raise HTTPException(404, f"Plonkit has no guide for {name}.")

    # Sections = what the Plonkit page shows: each step, plus each "divider" inside a step
    # (Landscape, Infrastructure...). A section is only listed once it has a clue we can import,
    # and `index` is where that first clue sits in `tips`.
    tips = []
    sections: list[dict] = []
    for step_no, step in enumerate(guide.get("steps") or [], start=1):
        pending = _section_title(step, step_no)  # title waiting for its first importable clue
        for item in step.get("items") or []:
            kind = item.get("kind")
            if kind == "divider":
                pending = _title_of(item) or pending
                continue
            if kind != "tip":
                continue
            data = item.get("data") or {}
            image_url = (data.get("image") or {}).get("imageUrl")
            text = clean_text(data.get("text") or [])
            if not text or not image_url:
                continue  # nothing to import without text and a picture
            if pending is not None:
                sections.append({"step": len(sections) + 1, "title": pending, "index": len(tips)})
                pending = None
            tags = item.get("tags") or []
            category, title, location = classify_full(text, category_for(text, tags), name)
            tips.append(
                {
                    "id": item.get("id") or uuid4().hex[:8],
                    "step": step_no,
                    "text": text,
                    "tags": item.get("tags") or [],
                    "image_url": image_url,
                    "link": _find_link(data),
                    "category": category,
                    "title": title,
                    "location": location,
                }
            )
    if not tips:
        raise HTTPException(404, f"The Plonkit guide for {name} has no clues to import.")

    # From the first "Regional ..." or "Spotlight" section on, every clue is expected to be regional.
    reg_from = next(
        (
            x["index"]
            for x in sections
            if re.match(r"\s*(?:region|regional|spotlight)\b", x["title"], re.IGNORECASE)
        ),
        None,
    )

    token = uuid4().hex
    s = {
        "cc": cc,
        "page_url": page_url,
        "tips": tips,
        "tasks": {},
        "meta": {},
        "lock": asyncio.Lock(),
        "wait_until": 0.0,
        "reg_from": reg_from,
        "last": time.monotonic(),  # when Plonkit was last called
    }
    s["warm"] = asyncio.create_task(_warm(ctx, cc))
    _SESSIONS[token] = s
    while len(_SESSIONS) > MAX_SESSIONS:
        _close(_SESSIONS.popitem(last=False)[1])
    clues = [
        {"index": i, "category": t["category"], "title": t["title"]} for i, t in enumerate(tips)
    ]
    return {"token": token, "total": len(tips), "sections": sections, "clues": clues}


@router.get("/import/plonkit/{token}/wait")
async def plonkit_wait(token: str):
    """Seconds left before the next clue can be downloaded."""
    s = _SESSIONS.get(token)
    if s is None:
        raise HTTPException(410, "That import expired. Start it again.")
    return {"seconds": max(0, math.ceil(s["wait_until"] - time.time()))}


@router.get("/import/plonkit/{token}/{index}")
async def plonkit_clue(token: str, index: int):
    s = _SESSIONS.get(token)
    if s is None:
        raise HTTPException(410, "That import expired. Start it again.")
    n = len(s["tips"])
    if not 0 <= index < n:
        raise HTTPException(404, "No such clue")
    tip = s["tips"][index]
    category = tip["category"]
    return {
        "index": index,
        "total": n,
        "id": tip["id"],
        "step": tip["step"],
        "info": tip["text"],
        "category": category,
        "location": tip["location"] or DEFAULT_LOCATION.get(category, "Anywhere"),
        "source": f"{s['page_url']}#{tip['step']}",
        "link": tip["link"],
        "zone": s["reg_from"] is not None and index >= s["reg_from"],
    }


@router.get("/import/plonkit/{token}/{index}/image")
async def plonkit_image(token: str, index: int, request: Request):
    """Downloads the clue's Plonkit image (only now, when it is needed) and returns it."""
    ctx = get_ctx(request)
    s = _SESSIONS.get(token)
    if s is None:
        raise HTTPException(410, "That import expired. Start it again.")
    if not 0 <= index < len(s["tips"]):
        raise HTTPException(404, "No such clue")
    for j in [j for j in s["tasks"] if j != index]:  # clues the user already moved away from
        s["tasks"].pop(j).cancel()
    cur = _task(ctx, s, index)
    meta = s["meta"][index]
    waiter = asyncio.ensure_future(meta["ev"].wait())
    try:  # answer as soon as the size is known (or the download ended): the body follows
        await asyncio.wait({waiter, cur}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        waiter.cancel()
    if cur.cancelled():
        raise HTTPException(409, "Superseded")
    headers = {}
    if meta["size"]:
        headers = {"X-Image-Width": str(meta["size"][0]), "X-Image-Height": str(meta["size"][1])}
    if cur.done():
        res = cur.result()
        if "error" in res:
            s["tasks"].pop(index, None)
            raise HTTPException(502, res["error"])
        return Response(res["data"], media_type=res["media"], headers=headers)

    async def body():
        try:
            res = await cur
        except asyncio.CancelledError:
            if cur.cancelled():
                msg = "Superseded"
                raise RuntimeError(msg) from None
            raise
        if "error" in res:
            s["tasks"].pop(index, None)
            raise RuntimeError(res["error"])
        yield res["data"]

    return StreamingResponse(body(), media_type=meta["media"], headers=headers)


@router.delete("/import/plonkit/{token}")
async def plonkit_stop(token: str):
    s = _SESSIONS.pop(token, None)
    if s:
        _close(s)
    return {"ok": True}
