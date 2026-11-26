"""anki_export.py — Send one clue to Anki: national ("From which country is this X?" or its inverse) or regional (map selection)."""

import base64
import html
import re
from pathlib import Path

import geopandas as gpd
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from src.anki.anki_connect import AnkiNotRunningError
from src.api.deps import get_ctx
from src.explore.country_clues.clues import CLUES_DIR, GENERAL_TAGS, _cc, _load  # noqa: PLC2701
from src.geo.anki_map import overlay_regions

router = APIRouter()

DECK = "GeoGuessr"
TYPE_MODEL = "Basic (type in the answer)"
BASIC_MODEL = "Basic"
NOT_RUNNING = "Anki isn't reachable. Open Anki with AnkiConnect installed."
MAPS_DIR = (
    Path(__file__).parents[3] / "data" / "maps"
)  # base country maps: {CC}.svg (made by country_to_svg)


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


def _plain(text: str) -> str:
    s = re.sub(r"\[([^\]]+)\]\(https?://[^\s)]+\)", r"\1", text)
    return " ".join(re.sub(r"\*\*|__|\*", "", s).split())


def _regions(cc: str, clue: dict) -> tuple[dict, bool]:
    """({name: lon/lat geometry}, has_names) of a regional clue with polygons."""
    name = clue.get("polygons")
    if not name:
        raise HTTPException(
            422, "Only national clues and regional clues with polygons can be sent to Anki."
        )
    gdf = gpd.read_parquet(CLUES_DIR / cc / name)
    geoms = shapely.make_valid(gdf.geometry.to_numpy())
    if "name" in gdf.columns:
        names = gdf["name"].fillna("").astype(str).to_numpy()
        return {n: shapely.union_all(geoms[names == n]) for n in dict.fromkeys(names)}, True
    return {"Stain": shapely.union_all(geoms)}, False


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
        regions, named = _regions(cc, clue)
        base = MAPS_DIR / f"{cc}.svg"
        if not base.is_file():
            raise HTTPException(
                404, f"No base map for {cc}. Make it with country_to_svg into {base}."
            )
        try:
            svg = overlay_regions(base.read_text(encoding="utf-8"), regions)
        except ValueError as e:
            raise HTTPException(422, str(e)) from e
        multi = named and len(regions) > 1
        return {
            "tags": tags,
            "cc": cc,
            "reg": True,
            "multi": multi,
            "question": f"Where in {country}: {_plain(clue['info'])}",
            "answer": "\n".join(regions) if named else country,
            "image": None,
            "svg": svg,
        }
    if invert:
        return {
            "tags": tags,
            "cc": cc,
            "question": f"How is the {what} from {country}?",
            "answer": clue["info"],
            "image": None,
        }
    return {
        "tags": tags,
        "cc": cc,
        "question": f"From which country is this {what}?",
        "answer": country,
        "image": clue.get("image"),
    }


async def _add_regional(anki, deck: str, card: dict, body) -> None:
    """Map selection note (Single / Multiple Map Selection). The note type is created only if it doesn't exist."""
    multi = card["multi"]
    model = "Multiple Map Selection" if multi else "Single Map Selection"
    answer_field = "Answers" if multi else "Answer"
    await anki.ensure_deck(deck)
    await anki.ensure_model(
        model,
        ["Question", "SvgFile", answer_field, "Highlight"],
        [
            {
                "Name": "Card 1",
                "Front": '{{Question}}<br><img src="{{SvgFile}}">',
                "Back": "{{FrontSide}}<hr id=answer>{{" + answer_field + "}}",
            }
        ],
    )
    name = f"clue_{card['cc']}_{body.clue_id}.svg"
    await anki.store_media(name, base64.b64encode(card["svg"].encode("utf-8")).decode())
    text = (body.answer or "").strip() or card["answer"]
    lines = "<br>".join(html.escape(x.strip()) for x in text.splitlines() if x.strip())
    question = (body.question or "").strip() or card["question"]
    await anki.add_note(
        deck,
        model,
        {
            "Question": html.escape(question),
            "SvgFile": name,
            answer_field: lines,
            "Highlight": "true" if body.highlight else "false",
        },
        card["tags"],
    )


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
