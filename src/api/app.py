"""app.py — FastAPI backend. Run: uvicorn src.api.app:app --port 8000."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routers import analysis, history, settings, status, sync
from src.app_context import AppContext
from src.i18n.lang import load as load_lang

DIST = Path(__file__).parents[2] / "frontend" / "dist"


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

for router_module in (status, sync, analysis, history, settings):
    app.include_router(router_module.router, prefix="/api")

# Must stay LAST: a mount at "/" swallows any route registered after it.
if DIST.exists():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="web")
