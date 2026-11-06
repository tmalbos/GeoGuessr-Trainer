"""status.py — Health/readiness of the API and the resources it needs."""

from fastapi import APIRouter, Request, Response

from src.api.deps import get_ctx
from src.geoguessr.cookie_store import load_cookie

router = APIRouter()


def _geo_state(ctx) -> str:
    task = ctx._geodata_task  # noqa: SLF001
    if ctx.local_geo is not None:
        return "ready"
    if task is None:
        return "starting"
    if task.done():
        return "error" if task.cancelled() or task.exception() is not None else "loading"
    return "loading"


@router.get("/health")
async def health():
    """Liveness: the process is serving. Touches no resource."""
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request, response: Response):
    """Readiness: 200 only when every startup resource is up; 503 with the per-check detail until then."""
    ctx = get_ctx(request)
    db = ctx._db_adapter is not None and await ctx._db_adapter.check_connection()  # noqa: SLF001
    checks = {"db": db, "geodata": _geo_state(ctx), "http": ctx.http_client is not None}
    ok = db and checks["geodata"] == "ready" and checks["http"]
    if not ok:
        response.status_code = 503
    return {"ready": ok, "checks": checks}


@router.get("/status")
async def status(request: Request):
    ctx = get_ctx(request)
    geo = _geo_state(ctx)
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
