"""study_scripts.py — Serves GeoNames dumps for the Study > Scripts trainer."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

GEONAMES_DIR = Path(__file__).parents[3] / "data" / "study" / "scripts" / "geonames"

router = APIRouter()


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
