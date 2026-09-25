"""app.py — FastAPI backend. Run: uvicorn src.api.app:app --port 8000."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.anki.generator import wait_for_anki
from src.core.api import CookieExpiredError
from src.core.app_context import AppContext
from src.core.auth import load_cookie, refresh_cookie, save_cookie
from src.core.calculator import _CHILD_LEVEL, _confusion, analyze
from src.core.events import Event
from src.core.stats import available_levels, build_groups, load_rounds
from src.core.sync import DEFAULT_USER_ID, sync_from_feed
from src.i18n.lang import load as load_lang

MIN_ROUNDS = 10
DIST = Path(__file__).parents[2] / "frontend" / "dist"


class SyncJob:
    """One sync run. Events are kept so a page refresh can replay them."""

    def __init__(self) -> None:
        self.events: list[Event] = []
        self.running = True
        self.cond = asyncio.Condition()
        self.task: asyncio.Task | None = None

    async def emit(self, ev: Event) -> None:
        async with self.cond:
            self.events.append(ev)
            self.cond.notify_all()


async def _run_sync(ctx: AppContext, job: SyncJob) -> None:
    async def fail(msg: str) -> None:
        await job.emit(Event("error", {"message": msg}))

    try:
        if not load_cookie():
            return await fail("No GeoGuessr cookie set. Add one in Settings.")
        if not await wait_for_anki(ctx.anki_client):
            return await fail("Anki is not reachable. Open Anki with AnkiConnect installed.")
        await ctx.ecoregion_ready.wait()
        if ctx.ecoregion_gdf is None:
            return await fail("Ecoregion shapefile failed to load. Check Ecoregions2017/.")

        for _ in range(2):
            client = ctx.create_geoguessr_client()
            try:
                await sync_from_feed(
                    client,
                    db=ctx.db_adapter,
                    geo_client=ctx.geo_client,
                    anki_client=ctx.anki_client,
                    http_client=ctx.http_client,
                    emit=job.emit,
                )
                break
            except CookieExpiredError:
                await job.emit(
                    Event("log", {"message": "Cookie expired, renewing...", "level": "warn"})
                )
                try:
                    await refresh_cookie()
                except RuntimeError:
                    return await fail("Could not refresh the cookie. Paste a new one in Settings.")
            finally:
                await client.aclose()
    except Exception as e:  # noqa: BLE001
        await fail(f"Unexpected error: {e}")
    finally:
        job.running = False
        await job.emit(Event("done"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    await load_lang(os.environ.get("GEOGUESSR_LANG", "en"))
    app.state.ctx = AppContext(db_dsn=os.environ.get("PG_DSN", ""))
    app.state.job = None
    await app.state.ctx.init()
    yield
    await app.state.ctx.aclose()


app = FastAPI(lifespan=lifespan)


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


# ── Status ──────────────────────────────────────────────────────────────────
@app.get("/api/status")
async def status(request: Request):
    ctx = _ctx(request)
    task = ctx._ecoregion_task  # noqa: SLF001
    if ctx.ecoregion_gdf is not None:
        eco = "ready"
    elif task is not None and task.done() and task.exception():
        eco = "error"
    else:
        eco = "loading"
    anki = False
    if ctx.http_client is not None:
        try:
            r = await ctx.http_client.post(
                "http://127.0.0.1:8765", json={"action": "version", "version": 6}, timeout=2
            )
            anki = r.status_code == 200
        except Exception:  # noqa: BLE001
            anki = False
    job = request.app.state.job
    return {
        "db": await ctx.db_adapter.check_connection(),
        "anki": anki,
        "ecoregions": eco,
        "cookie": bool(load_cookie()),
        "sync_running": bool(job and job.running),
    }


# ── Sync ────────────────────────────────────────────────────────────────────
@app.post("/api/sync", status_code=202)
async def start_sync(request: Request):
    job = request.app.state.job
    if job and job.running:
        raise HTTPException(409, "A sync is already running")
    job = request.app.state.job = SyncJob()
    job.task = asyncio.create_task(_run_sync(_ctx(request), job))
    return {"started": True}


@app.get("/api/sync/events")
async def sync_events(request: Request):
    job: SyncJob | None = request.app.state.job
    if job is None:
        raise HTTPException(404, "No sync has run yet")

    async def stream():
        i = 0
        while True:
            async with job.cond:
                await job.cond.wait_for(lambda: len(job.events) > i)
            ev = job.events[i]
            i += 1
            yield f"data: {json.dumps(asdict(ev), default=str)}\n\n"
            if ev.type == "done":
                return

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Analysis ────────────────────────────────────────────────────────────────
@app.get("/api/analysis/levels")
async def levels(request: Request):
    lv = await available_levels(_ctx(request).db_adapter, MIN_ROUNDS)
    return [{"level": k, "label": label, "rounds": n} for k, label, n in lv]


@app.get("/api/analysis/{level}")
async def analysis(level: str, request: Request):
    rounds = await load_rounds(_ctx(request).db_adapter)
    geo = None if level == "general" else level
    result = analyze(rounds, geo)
    groups = build_groups(rounds, geo)
    child = _CHILD_LEVEL.get(level)
    zones = []
    for name, s in result.zones.items():
        zr = groups.get(name, [])
        z = asdict(s) | {
            "name": name,
            "series": [r["distance_km"] for r in zr[-100:]],
            "confusions": [],
            "child_confusions": [],
        }
        if geo:
            z["confusions"] = _confusion(zr, geo, top_n=1)
            inside = [r for r in zr if (r.get("real_geo") or {}).get(geo) == name]
            z["child_level"] = child
            z["child_confusions"] = _confusion(inside, child, top_n=3) if child else []
        zones.append(z)
    zones.sort(key=lambda z: z["score_high"] if z["score_high"] is not None else 9999)
    return {"level": level, "label": result.level_label, "zones": zones}


# ── Settings ────────────────────────────────────────────────────────────────
class CookieBody(BaseModel):
    cookie: str


class LangBody(BaseModel):
    lang: str


class UserBody(BaseModel):
    user_id: str


@app.get("/api/settings")
async def get_settings():
    return {
        "lang": os.environ.get("GEOGUESSR_LANG", "en"),
        "user_id": os.environ.get("GEOGUESSR_USER_ID") or DEFAULT_USER_ID,
        "cookie": bool(load_cookie()),
    }


@app.put("/api/settings/cookie")
async def put_cookie(body: CookieBody):
    if not body.cookie.strip():
        raise HTTPException(422, "Cookie is empty")
    save_cookie(body.cookie.strip())
    return {"ok": True}


@app.post("/api/settings/cookie/refresh")
async def refresh():
    try:
        return {"ok": bool(await refresh_cookie())}
    except RuntimeError as e:
        raise HTTPException(502, str(e).strip()) from e


@app.put("/api/settings/lang")
async def put_lang(body: LangBody):
    await load_lang(body.lang)
    os.environ["GEOGUESSR_LANG"] = body.lang
    return {"ok": True}


@app.put("/api/settings/user")
async def put_user(body: UserBody):
    os.environ["GEOGUESSR_USER_ID"] = body.user_id.strip()
    return {"ok": True}


if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
