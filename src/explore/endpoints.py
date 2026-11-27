"""endpoints.py — Explore-wide lookups: the coverage state of each country and the quick facts the
Explore sidebar shows for one.

Coverage comes from data/coverage.txt (edit it by hand). Everything else is read from the country table.
"""

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from src.api.deps import get_ctx

COVERAGE_FILE = Path(__file__).parents[2] / "data" / "coverage.txt"
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


@router.get("/countries/{code}/info")
async def country_info(code: str, request: Request):
    """Quick facts for the Explore sidebar. Everything is null/empty if the country isn't in the table.

    domain: like '.DO'. driving_side: 'L' or 'R'. currencies: symbols, e.g. ['$'].
    """
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(422, "Invalid country code")
    row = await get_ctx(request).db_adapter.fetch_one(
        "SELECT web_domain, driving_side, currency_symbols FROM country WHERE code = $1",
        code.upper(),
    )
    row = row or {}
    domain = (row.get("web_domain") or "").strip().lstrip(".")
    side = (row.get("driving_side") or "").strip().upper()
    return {
        "domain": f".{domain.upper()}" if domain else None,
        "driving_side": side if side in {"L", "R"} else None,
        "currencies": list(row.get("currency_symbols") or []),
    }
