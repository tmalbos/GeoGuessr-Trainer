"""
anki_connect.py — Wrapper async para AnkiConnect API (puerto 8765).
"""

import httpx

ANKI_URL = "http://127.0.0.1:8765"


class AnkiNotRunningError(Exception):
    pass


class AnkiConnectClient:
    """Wrapper for the AnkiConnect API. Receives an AsyncClient for HTTP."""

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._client = http_client

    async def _invoke(self, action: str, **params) -> int | None:
        payload = {"action": action, "version": 6, "params": params}
        try:
            r = await self._client.post(ANKI_URL, json=payload)
            response = r.json()
        except httpx.ConnectError as e:
            raise AnkiNotRunningError() from e

        if response.get("error"):
            raise Exception(f"AnkiConnect: {response['error']}")
        return response["result"]

    async def is_running(self) -> bool:
        try:
            await self._invoke("version")
            return True
        except AnkiNotRunningError:
            return False

    async def ensure_deck(self, deck_name: str):
        await self._invoke("createDeck", deck=deck_name)

    async def ensure_model(self, model_name: str, fields: list[str], card_templates: list[dict]):
        existing = await self._invoke("modelNames")
        if model_name not in existing:
            await self._invoke(
                "createModel",
                modelName=model_name,
                inOrderFields=fields,
                css="",
                cardTemplates=card_templates,
            )

    async def note_exists(self, deck: str, tags: list) -> bool:
        tags_filter = " tag:".join(tags)
        results = await self._invoke("findNotes", query=f'deck:"{deck}" tag:{tags_filter}')
        return len(results) > 0

    async def add_note(self, deck: str, model: str, fields: dict, tags: list[str]) -> int | None:
        return await self._invoke(
            "addNote",
            note={
                "deckName": deck,
                "modelName": model,
                "fields": fields,
                "options": {"allowDuplicate": False},
                "tags": tags,
            },
        )
