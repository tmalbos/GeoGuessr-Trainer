"""history.py — Played-game history with filters, sorting and pagination."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request

from src.api.deps import get_ctx

router = APIRouter()


def _time_limit_mode_value(time_limit: str) -> tuple[str, int | None]:
    if time_limit in {"any", ""}:
        return "any", None
    if time_limit == "null":
        return "null", None
    return "value", int(time_limit)


@router.get("/history")
async def history(
    request: Request,
    match_type: str | None = None,
    move_type: str | None = None,
    time_limit: str = "any",
    min_score: int | None = None,
    max_score: int | None = None,
    sort_by: str = "date",
    sort_dir: str = "desc",
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
):
    mode, value = _time_limit_mode_value(time_limit)
    try:
        items, total = await get_ctx(request).db_adapter.fetch_game_history(
            match_type,
            move_type,
            mode,
            value,
            min_score,
            max_score,
            sort_by,
            sort_dir,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    return {"items": items, "total": total, "page": page, "page_size": page_size}
