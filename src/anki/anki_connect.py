"""
anki_connect.py — Wrapper para AnkiConnect API (puerto 8765).
"""

import json
import urllib.error
import urllib.request

ANKI_URL = "http://127.0.0.1:8765"


class AnkiNotRunningError(Exception):
    pass


def _invoke(action: str, **params) -> int | None:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    try:
        response = json.load(
            urllib.request.urlopen(urllib.request.Request(ANKI_URL, payload), timeout=5)
        )
    except urllib.error.URLError as e:
        raise AnkiNotRunningError() from e

    if response.get("error"):
        raise Exception(f"AnkiConnect: {response['error']}")
    return response["result"]


def is_running() -> bool:
    try:
        _invoke("version")
        return True
    except AnkiNotRunningError:
        return False


def ensure_deck(deck_name: str):
    _invoke("createDeck", deck=deck_name)


def ensure_model(model_name: str, fields: list[str], card_templates: list[dict]):
    existing = _invoke("modelNames")
    if model_name not in existing:
        _invoke(
            "createModel",
            modelName=model_name,
            inOrderFields=fields,
            css="",
            cardTemplates=card_templates,
        )


def note_exists(deck: str, tags: list) -> bool:
    tags_filter = " tag:".join(tags)
    results = _invoke("findNotes", query=f'deck:"{deck}" tag:{tags_filter}')
    return len(results) > 0


def add_note(deck: str, model: str, fields: dict, tags: list[str]) -> int | None:
    return _invoke(
        "addNote",
        note={
            "deckName": deck,
            "modelName": model,
            "fields": fields,
            "options": {"allowDuplicate": False},
            "tags": tags,
        },
    )
