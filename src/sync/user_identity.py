"""user_identity.py — Which GeoGuessr account the sync pipeline reads from."""

import os

DEFAULT_USER_ID = "68daf785000ba2a268744f99"


def user_id() -> str:
    return os.environ.get("GEOGUESSR_USER_ID") or DEFAULT_USER_ID
