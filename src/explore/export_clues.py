"""export_clues.py — export clues as one self-contained `.cluepack` file (a zip)."""

import io
import json
import re
import zipfile
from collections import Counter
from datetime import date

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from src.explore.country_clues import decks as deck_store
from src.explore.country_clues.clue_relations import _set_rels
from src.explore.country_clues.clues import (
    ASSET_RE,
    CLUES_DIR,
    KINDS,
    _load,
    _valid,
)

router = APIRouter()

FORMAT = 1
# The fields of a clue that travel in a pack (files are handled apart; overlay / score / medal are rebuilt).
KEEP = (
    "id", "scope", "info", "tags", "frequency", "ease", "reliability", "blur", "view",
    "max_height", "source", "relations", "relation_notes", "deck", "show_names",
    "prefix_colors",
)  # fmt: skip


def _countries() -> list[str]:
    return [
        d.name.upper()
        for d in sorted(CLUES_DIR.glob("??"))
        if d.is_dir() and re.fullmatch(r"[A-Za-z]{2}", d.name)
    ]


@router.get("/clue-pack/decks")
def pack_decks():
    counts = Counter(deck_store.clean(c.get("deck")) for cc in _countries() for c in _load(cc))
    counts.pop("", None)
    return [{"name": n, "count": k} for n, k in sorted(counts.items())]


class ExportBody(BaseModel):
    decks: list[str]


@router.post("/clue-pack/export")
def pack_export(body: ExportBody):
    want = {deck_store.clean(d) for d in body.decks} - {""}
    if not want:
        raise HTTPException(422, "Pick at least one deck.")
    chosen: dict[str, list[dict]] = {}
    for cc in _countries():
        items = [c for c in _load(cc) if deck_store.clean(c.get("deck")) in want]
        if items:
            chosen[cc] = items
    keys = {(cc, c["id"]) for cc, items in chosen.items() for c in items}

    buf = io.BytesIO()
    used: set[str] = set()
    countries: list[str] = []
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for cc, items in chosen.items():
            out = []
            for c in items:
                entry = {k: c[k] for k in KEEP if c.get(k) is not None}
                # Only relations that point inside the pack (otherwise the pack would be invalid).
                _set_rels(
                    entry, [r for r in c.get("relations") or [] if (r["cc"], r["id"]) in keys]
                )
                files = {}
                for kind in KINDS:
                    name = c.get(kind)
                    path = CLUES_DIR / cc / name if name else None
                    if path is not None and ASSET_RE.fullmatch(name) and path.is_file():
                        entry[kind] = name
                        files[name] = path
                if not _valid(entry):
                    continue
                for name, path in files.items():
                    z.write(path, f"{cc}/files/{name}")
                used.add(deck_store.clean(c.get("deck")))
                out.append(entry)
            if out:
                countries.append(cc)
                z.writestr(
                    f"{cc}/clues.yaml",
                    yaml.safe_dump(out, allow_unicode=True, sort_keys=False, width=100),
                )
        z.writestr(
            "manifest.json",
            json.dumps({"format": FORMAT, "decks": sorted(used), "countries": countries}),
        )
    if not countries:
        raise HTTPException(404, "Those decks have no clues yet.")
    return Response(
        buf.getvalue(),
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="clues-{date.today().isoformat()}.cluepack"'
        },
    )
