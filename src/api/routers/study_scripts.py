"""study_scripts.py — Serves GeoNames dumps and stores timed-session results for the Scripts trainer."""

import json
import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

DATA_DIR = Path(__file__).parents[3] / "data" / "study" / "scripts"
GEONAMES_DIR = DATA_DIR / "geonames"
SESSIONS_FILE = DATA_DIR / "sessions.json"

router = APIRouter()


class SessionBody(BaseModel):
    script: str = Field(pattern=r"^[a-z]{2,8}$")
    good: int = Field(ge=0)
    bad: int = Field(ge=0)
    skipped: int = Field(ge=0)
    time_limit_sec: int = Field(gt=0)


def _read_sessions() -> list[dict]:
    if not SESSIONS_FILE.is_file():
        return []
    try:
        return json.loads(SESSIONS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"{SESSIONS_FILE.name} is corrupted") from e


@router.get("/study/scripts/geonames/{code}")
async def geonames(code: str):
    if not re.fullmatch(r"[A-Za-z]{2}", code):
        raise HTTPException(422, "Invalid country code")
    path = GEONAMES_DIR / f"{code.upper()}.txt"
    if not path.is_file():
        raise HTTPException(404, f"{code.upper()}.txt not found in data/study/scripts/geonames/")
    return FileResponse(
        path,
        media_type="text/plain; charset=utf-8",
        headers={"Cache-Control": "public, max-age=86400"},
    )


@router.get("/study/scripts/sessions")
def list_sessions(script: str | None = None):
    """Timed sessions, newest first. Optionally filtered by script id."""
    sessions = _read_sessions()
    if script:
        sessions = [s for s in sessions if s["script"] == script]
    return sessions[::-1]


@router.post("/study/scripts/sessions", status_code=201)
def add_session(body: SessionBody):
    sessions = _read_sessions()
    record = {"date": datetime.now().isoformat(timespec="seconds"), **body.model_dump()}
    sessions.append(record)
    SESSIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSIONS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
    tmp.replace(SESSIONS_FILE)
    return record
