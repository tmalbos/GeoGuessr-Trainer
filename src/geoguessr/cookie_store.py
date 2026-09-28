"""cookie_store.py — Persist the GeoGuessr _ncfa cookie on disk."""

import pathlib

COOKIE_FILE = "geoguessr_cookie.txt"


def load_cookie() -> str | None:
    if pathlib.Path(COOKIE_FILE).exists():
        with pathlib.Path(COOKIE_FILE).open(encoding="utf-8") as f:
            cookie = f.read().strip()
        if cookie:
            return cookie

    return None


def save_cookie(cookie: str) -> None:
    pathlib.Path(COOKIE_FILE).write_text(cookie, encoding="utf-8")
