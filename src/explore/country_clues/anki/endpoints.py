"""endpoints.py — Send one clue to Anki: national ("From which country is this X?" or its inverse) or regional (map selection)."""

import base64
import html
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.api.deps import get_ctx
from src.explore.country_clues.anki.anki_connect import AnkiNotRunningError
from src.explore.country_clues.anki.cards.national import national_card
from src.explore.country_clues.anki.cards.regional import _add_regional, regional_card
from src.explore.country_clues.clues import CLUES_DIR, GENERAL_TAGS, _cc, _load

router = APIRouter()

DECK = "GeoGuessr"
TYPE_MODEL = "Basic (type in the answer)"
BASIC_MODEL = "Basic"
NOT_RUNNING = "Anki isn't reachable. Open Anki with AnkiConnect installed."


async def _models(anki) -> dict[str, tuple[str, str, str] | None]:
    """The two built-in note types as (name, front field, back field).

    Anki stores the names (and field names) in the language the collection was created in, so the
    English names are tried first and otherwise the note types are recognised by their structure.
    """
    names = await anki.model_names()
    found: dict[str, tuple[str, str, str] | None] = {"type": None, "basic": None}
    for kind, english in (("type", TYPE_MODEL), ("basic", BASIC_MODEL)):
        if english in names:
            fields = await anki.model_fields(english)
            if len(fields) == 2:
                found[kind] = (english, fields[0], fields[1])
    for name in names:
        if all(found.values()):
            break
        fields = await anki.model_fields(name)
        if len(fields) != 2:
            continue
        templates = await anki.model_templates(name)
        if len(templates) != 1:
            continue
        text = "".join(str(v) for t in templates.values() for v in t.values())
        if "{{cloze:" in text:
            continue
        kind = "type" if "{{type:" in text else "basic"
        if found[kind] is None:
            found[kind] = (name, fields[0], fields[1])
    return found


def _rich(text: str) -> str:
    """The clue's **bold** / *italic* / __underline__ / [link](url) text as HTML."""
    s = html.escape(text)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"__(.+?)__", r"<u>\1</u>", s)
    s = re.sub(r"\*(.+?)\*", r"<i>\1</i>", s)
    s = re.sub(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", r'<a href="\2">\1</a>', s)
    return s.replace("\n", "<br>")


async def _card(request: Request, cc: str, clue_id: str, invert: bool) -> dict:
    cc = _cc(cc)
    clue = next((c for c in _load(cc) if c.get("id") == clue_id), None)
    if clue is None:
        raise HTTPException(404, "Clue not found")
    reg = clue.get("scope") == "reg"
    row = await get_ctx(request).db_adapter.fetch_one(
        "SELECT name FROM country WHERE code = $1", cc
    )
    country = row["name"] if row else cc
    cat = next((t for t in clue.get("tags", []) if t in GENERAL_TAGS), "clue")
    what = cat.lower()
    c_tag, k_tag = (re.sub(r"\s+", "_", x.strip()) for x in (country, cat))
    tags = [
        "GeoGuessr",
        f"Country::{c_tag}::{'Regional' if reg else 'National'}::{k_tag}",
        f"Scope::{'Regional' if reg else 'National'}",
        f"Category::{k_tag}",
    ]
    if reg:
        return regional_card(cc, country, tags, clue)
    return national_card(cc, country, what, tags, clue, invert)


@router.get("/anki/decks")
async def anki_decks(request: Request):
    try:
        names = await get_ctx(request).anki_client.deck_names()
    except AnkiNotRunningError as e:
        raise HTTPException(503, NOT_RUNNING) from e
    return {"decks": names if DECK in names else [DECK, *names], "default": DECK}


@router.get("/anki/preview/{cc}/{clue_id}")
async def anki_preview(cc: str, clue_id: str, request: Request, invert: bool = False):
    return await _card(request, cc, clue_id, invert)


class ExportBody(BaseModel):
    cc: str
    clue_id: str
    invert: bool = False
    highlight: bool = False
    deck: str = ""
    question: str | None = None
    answer: str | None = None


@router.post("/anki/export")
async def anki_export(body: ExportBody, request: Request):
    card = await _card(request, body.cc, body.clue_id, body.invert)
    anki = get_ctx(request).anki_client
    deck = body.deck.strip() or DECK
    try:
        if card.get("reg"):
            await _add_regional(anki, deck, card, body)
            return {"ok": True}
        found = (await _models(anki))["basic" if body.invert else "type"]
        if found is None:
            raise HTTPException(422, "Couldn't find the note type in Anki.")  # noqa: TRY301
        model, front_field, back_field = found
        await anki.ensure_deck(deck)
        question = (body.question or "").strip() or card["question"]
        answer = (body.answer or "").strip() if body.invert else ""
        front = html.escape(question)
        if card["image"]:
            name = f"clue_{card['cc']}_{card['image']}"
            data = (CLUES_DIR / card["cc"] / card["image"]).read_bytes()
            await anki.store_media(name, base64.b64encode(data).decode())
            front += f'<br><img src="{name}">'
        await anki.add_note(
            deck,
            model,
            {front_field: front, back_field: _rich(answer or card["answer"])},
            card["tags"],
        )
    except AnkiNotRunningError as e:
        raise HTTPException(503, NOT_RUNNING) from e
    except HTTPException:
        raise
    except Exception as e:
        if "duplicate" in str(e).lower():
            raise HTTPException(409, "This card is already in Anki.") from e
        raise HTTPException(502, str(e)[:150]) from e
    return {"ok": True}
