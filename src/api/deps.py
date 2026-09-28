"""deps.py — FastAPI dependencies shared by all routers."""

from fastapi import Request

from src.app_context import AppContext


def get_ctx(request: Request) -> AppContext:
    return request.app.state.ctx
