"""countries.py — Small lookups on the country table."""

from fastapi import APIRouter, HTTPException, Request

from src.api.deps import get_ctx

router = APIRouter()


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
