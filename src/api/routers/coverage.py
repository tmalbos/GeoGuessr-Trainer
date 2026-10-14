"""coverage.py — Coverage state per country, read from data/coverage.txt (edit it by hand)."""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

COVERAGE_FILE = Path(__file__).parents[3] / "data" / "coverage.txt"
SECTIONS = {"no_coverage": "none", "official": "official", "trekker": "trekker",
            "unofficial": "unofficial", "speculated": "speculated"}  # fmt: skip

router = APIRouter()

_cache: dict = {"mtime": None, "data": {}}


def _parse(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    state: str | None = None
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("["):
            name = line.strip("[] ").lower().replace(" ", "_")
            if name not in SECTIONS:
                msg = f"coverage.txt line {n}: unknown section [{name}]"
                raise HTTPException(500, msg)
            state = SECTIONS[name]
            continue
        for code in re.split(r"[\s,;]+", line):
            if not code:
                continue
            if state is None:
                msg = f"coverage.txt line {n}: '{code}' is before any [section]"
                raise HTTPException(500, msg)
            if not re.fullmatch(r"[A-Za-z]{2}", code):
                msg = f"coverage.txt line {n}: '{code}' is not a 2-letter code"
                raise HTTPException(500, msg)
            out[code.upper()] = state  # a later section wins over an earlier one
    return out


@router.get("/coverage")
def coverage():
    """{coverage: {CC: state}} for the countries listed in the file. Re-read whenever the file changes."""
    if not COVERAGE_FILE.is_file():
        return {"coverage": {}}
    mtime = COVERAGE_FILE.stat().st_mtime
    if _cache["mtime"] != mtime:
        _cache["data"] = _parse(COVERAGE_FILE.read_text(encoding="utf-8"))
        _cache["mtime"] = mtime
    return {"coverage": _cache["data"]}
