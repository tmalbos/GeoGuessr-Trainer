"""sync.py — Start a sync job and stream its events (SSE)."""

import asyncio
import json
import logging
from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from src.api.deps import get_ctx
from src.app_context import AppContext
from src.geoguessr.client import CookieExpiredError
from src.geoguessr.cookie_extraction import refresh_cookie
from src.geoguessr.cookie_store import load_cookie
from src.shared.events import Event
from src.sync.orchestrator import sync_from_feed

logger = logging.getLogger("uvicorn.error")

router = APIRouter()


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


class SyncBody(BaseModel):
    match_types: list[str] = ["daily", "challenge", "duel"]


async def _run_sync(ctx: AppContext, job: SyncJob, match_types: set[str]) -> None:
    async def fail(msg: str, detail: str = "") -> None:
        # `message` is shown to the user; `detail` sits behind a "Details" toggle.
        await job.emit(Event("error", {"message": msg, "detail": detail}))

    try:
        if not load_cookie():
            return await fail("No GeoGuessr cookie set. Add one in Settings.")

        # if not await wait_for_anki(ctx.anki_client):
        #     return await fail("Anki is not reachable. Open Anki with AnkiConnect installed.")

        await ctx.geodata_ready.wait()

        if ctx.local_geo is None:
            return await fail(
                "Geo layers failed to load. Check data/geo/ (see scripts/convert_geo.py)."
            )

        for _ in range(2):
            client = ctx.create_geoguessr_client()
            try:
                await sync_from_feed(
                    client,
                    db=ctx.db_adapter,
                    geo_client=ctx.geo_client,
                    anki_client=ctx.anki_client,
                    http_client=ctx.http_client,
                    match_types=match_types,
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
    except Exception as e:
        logger.exception("Sync failed")
        return await fail(
            "Sync stopped unexpectedly. Check the server log for the full error.",
            f"{type(e).__name__}: {e}",
        )
    finally:
        job.running = False
        await job.emit(Event("done"))


@router.post("/sync", status_code=202)
async def start_sync(body: SyncBody, request: Request):
    job = request.app.state.job
    if job and job.running:
        raise HTTPException(409, "A sync is already running")
    job = request.app.state.job = SyncJob()
    job.task = asyncio.create_task(_run_sync(get_ctx(request), job, set(body.match_types)))
    return {"started": True}


@router.get("/sync/events")
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
