"""status.py — Health of DB, Anki, geo layers and cookie."""

from fastapi import APIRouter, Request

from src.api.deps import get_ctx
from src.geoguessr.cookie_store import load_cookie

router = APIRouter()


@router.get("/status")
async def status(request: Request):
    ctx = get_ctx(request)
    task = ctx._geodata_task  # noqa: SLF001
    if ctx.local_geo is not None:
        geo = "ready"
    elif task is not None and task.done() and task.exception():
        geo = "error"
    else:
        geo = "loading"
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
        "geodata": geo,
        "cookie": bool(load_cookie()),
        "sync_running": bool(job and job.running),
    }
