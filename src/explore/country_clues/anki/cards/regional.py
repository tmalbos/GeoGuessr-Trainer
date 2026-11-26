"""regional.py — The regional Anki card: map selection."""

import base64
import html
import re
from pathlib import Path

import geopandas as gpd
import shapely
from fastapi import HTTPException

from src.explore.country_clues.clues import CLUES_DIR
from src.geo.anki_map import overlay_regions

MAPS_DIR = (
    Path(__file__).parents[5] / "data" / "maps"
)  # base country maps: {CC}.svg (made by country_to_svg)


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


def regional_card(cc: str, country: str, tags: list[str], clue: dict) -> dict:
    """The regional card: a map selection over the clue's polygons."""
    regions, named = _regions(cc, clue)
    base = MAPS_DIR / f"{cc}.svg"
    if not base.is_file():
        raise HTTPException(404, f"No base map for {cc}. Make it with country_to_svg into {base}.")
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
