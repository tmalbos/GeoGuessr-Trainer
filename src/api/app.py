"""app.py — FastAPI backend. Run: uvicorn src.api.app:app --port 8000."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI

from src.api.routers import (
    analysis,
    history,
    osm_extract,
    pbf_admin,
    replay_report,
    settings,
    status,
    streetview,
    study_scripts,
    sync,
    technical,
)
from src.app_context import AppContext
from src.explore import endpoints as explore
from src.explore import export_clues, import_clues
from src.explore.country_clues import endpoints as clue_read
from src.explore.country_clues import regional_stain_areas as stain
from src.explore.country_clues.anki import endpoints as anki_export
from src.explore.country_clues.clue_imports import plonkit as plonkit_import
from src.explore.country_clues.edit_mode import endpoints as clue_edit
from src.i18n.lang import load as load_lang
from src.shared import prefs

DIST = Path(__file__).parents[2] / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    await load_lang(str(prefs.load().get("lang") or "en"))
    app.state.ctx = AppContext(db_dsn=os.environ.get("PG_DSN", ""))
    app.state.job = None
    await app.state.ctx.init()
    yield
    await app.state.ctx.aclose()


app = FastAPI(lifespan=lifespan)

for router_module in (
    status,
    sync,
    analysis,
    anki_export,
    history,
    settings,
    study_scripts,
    clue_read,
    clue_edit,
    export_clues,
    import_clues,
    explore,
    replay_report,
    streetview,
    technical,
    pbf_admin,
    osm_extract,
    stain,
    plonkit_import,
):
    app.include_router(router_module.router, prefix="/api")
