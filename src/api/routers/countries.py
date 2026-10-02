"""countries.py — Small lookups on the country table."""

from fastapi import APIRouter, HTTPException, Request

from src.api.deps import get_ctx

router = APIRouter()


@router.get("/countries/{code}/domain")
async def country_domain(code: str, request: Request):
    """Internet domain of a country, formatted like '.DO' (null if the country isn't in the table)."""
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(422, "Invalid country code")
    row = await get_ctx(request).db_adapter.fetch_one(
        "SELECT web_domain FROM country WHERE code = $1", code.upper()
    )
    domain = ((row or {}).get("web_domain") or "").strip().lstrip(".")
    return {"domain": f".{domain.upper()}" if domain else None}
