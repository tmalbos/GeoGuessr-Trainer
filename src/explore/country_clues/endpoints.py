"""endpoints.py — The routes that read a country's clues: the list, one file, and the summary /
subcontinents the world map and the relate picker use.
"""

import contextlib
import csv
import io
import zlib

import pyarrow.parquet as pq
from fastapi import APIRouter
from fastapi.responses import FileResponse, Response

from src.explore.country_clues.clues import (
    CLUES_DIR,
    _asset,
    _cc,
    _countries_with_clues,
    _load,
    _summary,
)
from src.explore.country_clues.regional_map_overlay import recolour
from src.explore.country_clues.subcontinents import SUBCONTINENTS
from src.shared import prefs

router = APIRouter()


# ── clues ──────────────────────────────────────────────────────────────────


@router.get("/clues/summary")
def clues_summary():
    """Countries that have at least one clue shown in the normal view (must stay above /clues/{cc})."""
    return {"countries": sorted(_summary())}


@router.get("/clues/subcontinents")
def clues_subcontinents():
    """[{id, label, countries, all}] where countries = the ones of that subcontinent that have clues,
    all = every country of the subcontinent.
    """
    have = _countries_with_clues()
    return [
        {
            "id": sid,
            "label": label,
            "countries": sorted(c for c in codes.split() if c in have),
            "all": sorted(codes.split()),
        }
        for sid, label, codes in SUBCONTINENTS
    ]


@router.get("/clues/{cc}")
def list_clues(cc: str):
    cc = _cc(cc)
    items = _load(cc)
    color = str(prefs.load().get("stain_color") or "#c8283a")
    depths = prefs.load().get("clue_depths") or {}
    for c in items:
        if f"{cc}/{c.get('id')}" in depths:
            c["depth"] = depths[f"{cc}/{c.get('id')}"]
        if c.get("csv"):
            p = CLUES_DIR / cc / c["csv"]
            if p.is_file():
                c["csv_rows"] = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8"))))
        if c.get(
            "polygons"
        ):  # drawn areas (name + color columns): the editor opens them straight in Draw
            with contextlib.suppress(Exception):
                names = set(pq.read_schema(CLUES_DIR / cc / c["polygons"]).names)
                c["drawn"] = {"name", "color"} <= names
                c["shaded"] = "drawn" in names  # made with the shade draw tool (photo clues)
        if any(c.get(k) for k in ("image", "svg", "overlay", "polygons", "lines", "points")):
            # rev (the file's mtime) busts the browser cache for ?v=: a file overwritten in place
            # keeps its name, so without this the card could show the old picture after an update.
            # Geometry files count too: a drawn clue has no image, and its colors live in the parquet.
            rev = 0
            for kind in ("image", "svg", "overlay", "polygons", "lines", "points"):
                p = CLUES_DIR / cc / c[kind] if c.get(kind) else None
                if p and p.is_file():
                    rev = max(rev, p.stat().st_mtime_ns)
            if rev:
                c["rev"] = rev + (zlib.crc32(color.encode()) if c.get("overlay") else 0)
    return items


@router.get("/clues/{cc}/files/{name}")
def clue_file(cc: str, name: str):
    # "no-cache" = the browser may keep it but must revalidate, so a file overwritten in place
    # (same name, new content) shows up right away.
    p = _asset(cc, name)
    # The overlay is baked in the default colour; paint it with the colour picked in Settings on the way out,
    # so a change shows up the next time any regional clue is viewed (the file itself is not rewritten).
    if name.endswith(".overlay.svg"):
        color = str(prefs.load().get("stain_color") or "#c8283a")
        return Response(
            recolour(p.read_text(encoding="utf-8"), color),
            media_type="image/svg+xml",
            headers={"Cache-Control": "no-cache"},
        )
    # An SVG has to be served as such for the card to render it as a picture.
    media = "image/svg+xml" if p.suffix == ".svg" else None
    return FileResponse(p, media_type=media, headers={"Cache-Control": "no-cache"})
