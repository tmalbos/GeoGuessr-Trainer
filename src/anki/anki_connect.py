"""
anki_connect.py — Wrapper async para AnkiConnect API (puerto 8765).
"""

import httpx

ANKI_URL = "http://127.0.0.1:8765"

_client = httpx.AsyncClient(timeout=5)


class AnkiNotRunningError(Exception):
    pass


async def _invoke(action: str, **params) -> int | None:
    payload = {"action": action, "version": 6, "params": params}
    try:
        r = await _client.post(ANKI_URL, json=payload)
        response = r.json()
    except httpx.ConnectError as e:
        raise AnkiNotRunningError() from e

    if response.get("error"):
        raise Exception(f"AnkiConnect: {response['error']}")
    return response["result"]


async def is_running() -> bool:
    try:
        await _invoke("version")
        return True
    except AnkiNotRunningError:
        return False


async def ensure_deck(deck_name: str):
    await _invoke("createDeck", deck=deck_name)


async def ensure_model(model_name: str, fields: list[str], card_templates: list[dict]):
    existing = await _invoke("modelNames")
    if model_name not in existing:
        await _invoke(
            "createModel",
            modelName=model_name,
            inOrderFields=fields,
            css="",
            cardTemplates=card_templates,
        )


async def note_exists(deck: str, tags: list) -> bool:
    tags_filter = " tag:".join(tags)
    results = await _invoke("findNotes", query=f'deck:"{deck}" tag:{tags_filter}')
    return len(results) > 0


async def add_note(deck: str, model: str, fields: dict, tags: list[str]) -> int | None:
    return await _invoke(
        "addNote",
        note={
            "deckName": deck,
            "modelName": model,
            "fields": fields,
            "options": {"allowDuplicate": False},
            "tags": tags,
        },
    )
