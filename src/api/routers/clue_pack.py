"""clue_pack.py — Export / import clues as one self-contained `.cluepack` file (a zip).

Layout of a pack:
    manifest.json          {"format": 1, "decks": [...], "countries": ["AR", ...]}
    {CC}/clues.yaml        that country's clues of the exported decks
    {CC}/files/<name>      every file those clues use (images, SVG, CSV, geometry)

Export keeps only the relations whose other end is inside the pack, and leaves out what import rebuilds
(overlay picture, score, medal).
Import matches clues by country + id: same country and id = the local clue is replaced (in place); anything
else is added (the same id in another country is just a new clue). Files are copied under fresh local names,
so they never collide with local ones. Decks the user doesn't have are added to the Settings list.
An import never deletes anything. A clue that can't be read (bad tags, no image / region) is skipped.

GET  /clue-pack/decks   -> [{name, count}] decks that have clues
POST /clue-pack/export  {decks: [...]} -> the pack
POST /clue-pack/import  raw pack bytes -> {added, updated, skipped}
"""

import io
import json
import logging
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.api.routers.clues import (
    _LOCK,
    AREA_KINDS,
    ASSET_RE,
    CLUES_DIR,
    GENERAL_TAGS,
    GEOM_KINDS,
    KINDS,
    LOCATION_TAGS,
    _apply_medal,
    _cc,
    _load,
    _next_name,
    _next_num,
    _overlay_name,
    _render_overlay,
    _save,
    _set_has,
    _set_rels,
    _sync_relations,
    _write_overlay,
)
from src.shared import decks as deck_store

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

FORMAT = 1
ID_RE = re.compile(r"[\w-]{1,100}")
REL_TYPES = {"same_as", "similar_to", "group"}
LEGACY_TAGS = {"Topography": "Landscape"}  # tags of packs made before the rename
# The fields of a clue that travel in a pack (files are handled apart; overlay / score / medal are rebuilt).
KEEP = (
    "id", "scope", "info", "tags", "frequency", "ease", "reliability", "blur",
    "max_height", "source", "relations", "relation_notes", "deck",
)  # fmt: skip


def _countries() -> list[str]:
    return [
        d.name.upper()
        for d in sorted(CLUES_DIR.glob("??"))
        if d.is_dir() and re.fullmatch(r"[A-Za-z]{2}", d.name)
    ]


def _valid(entry: dict) -> bool:
    if entry["scope"] == "nat":
        return "image" in entry
    return any(k in entry for k in AREA_KINDS)


# ── export ─────────────────────────────────────────────────────────────────


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


# ── import ─────────────────────────────────────────────────────────────────


def _rels(raw) -> list[dict]:
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        cc, cid = str(r.get("cc") or "").upper(), r.get("id")
        if (
            r.get("type") in REL_TYPES
            and re.fullmatch(r"[A-Z]{2}", cc)
            and isinstance(cid, str)
            and ID_RE.fullmatch(cid)
        ):
            out.append({"type": r["type"], "cc": cc, "id": cid})
    return out


def _build(c: dict, cc: str, z: zipfile.ZipFile, folder: Path) -> tuple[dict, list[Path]] | None:
    """A clue of the pack as a local entry, its files copied into `folder` under new names."""
    cid, scope = c.get("id"), c.get("scope")
    tags = c.get("tags") if isinstance(c.get("tags"), list) else []
    tags = [LEGACY_TAGS.get(t, t) if isinstance(t, str) else t for t in tags]
    loc = [t for t in tags if t in LOCATION_TAGS]
    gen = [t for t in tags if t in GENERAL_TAGS]
    info = str(c.get("info") or "").strip()
    if not (
        isinstance(cid, str)
        and ID_RE.fullmatch(cid)
        and scope in {"nat", "reg"}
        and len(loc) == 1
        and len(gen) == 1
        and info
    ):
        return None
    entry = {k: c[k] for k in KEEP if c.get(k) is not None}
    entry.update(tags=[loc[0], gen[0]], info=info)
    deck = deck_store.clean(c.get("deck"))
    if deck:
        entry["deck"] = deck
    else:
        entry.pop("deck", None)
    if not isinstance(entry.get("relation_notes"), dict):
        entry.pop("relation_notes", None)
    rels = _rels(c.get("relations"))
    if rels:
        entry["relations"] = rels
    else:
        entry.pop("relations", None)

    folder.mkdir(parents=True, exist_ok=True)
    num = _next_num(folder, scope, gen[0])  # one number for every file of this clue
    mine: list[Path] = []
    for kind in KINDS:
        name = c.get(kind)
        if not isinstance(name, str) or not ASSET_RE.fullmatch(name) or ".overlay." in name:
            continue
        if scope == "nat" and kind != "image":
            continue
        try:
            data = z.read(f"{cc}/files/{name}")
        except KeyError:
            continue
        out = folder / _next_name(folder, scope, gen[0], Path(name).suffix, num)
        out.write_bytes(data)
        mine.append(out)
        entry[kind] = out.name
    if not _valid(entry):
        for p in mine:
            p.unlink(missing_ok=True)
        return None
    if "image" not in entry:
        entry.pop("blur", None)
        entry.pop("max_height", None)
    return entry, mine


def _import(raw: bytes, request: Request) -> dict:
    if not raw:
        raise HTTPException(422, "Empty file")
    try:
        z = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as e:
        raise HTTPException(422, "That is not a clue pack.") from e
    local = get_ctx(request).local_geo
    bad = "That clue pack is damaged or not supported."
    with z:
        try:
            manifest = json.loads(z.read("manifest.json"))
            if manifest.get("format") != FORMAT:
                raise HTTPException(422, "That clue pack was made by a different version.")
            packs: dict[str, list[dict]] = {}
            for name in manifest["countries"]:
                code = _cc(name)
                data = yaml.safe_load(z.read(f"{code}/clues.yaml")) or []
                if not isinstance(data, list):
                    raise ValueError  # noqa: TRY301
                packs[code] = [c for c in data if isinstance(c, dict)]
        except (KeyError, ValueError, TypeError, AttributeError, yaml.YAMLError) as e:
            raise HTTPException(422, bad) from e

        counts = {"added": 0, "updated": 0, "skipped": 0}
        written: list[Path] = []
        stale: list[Path] = []
        touched: list[
            tuple[str, dict, list[dict]]
        ] = []  # (country, new entry, relations it had before)
        books: dict[str, list[dict]] = {}
        seen: set[tuple] = set()

        def items_of(code: str) -> list[dict]:
            if code not in books:
                books[code] = _load(code)
            return books[code]

        with _LOCK:
            try:
                for cc, clues in packs.items():
                    folder = CLUES_DIR / cc
                    items = items_of(cc)
                    for c in clues:
                        key = (cc, c.get("id"))
                        built = None if key in seen else _build(c, cc, z, folder)
                        seen.add(key)
                        if built is None:
                            counts["skipped"] += 1
                            continue
                        entry, files = built
                        written += files
                        at = next(
                            (i for i, o in enumerate(items) if o.get("id") == entry["id"]), None
                        )
                        if at is None:
                            items.append(entry)
                            counts["added"] += 1
                            before: list[dict] = []
                        else:
                            old = items[at]
                            items[at] = entry
                            counts["updated"] += 1
                            before = list(old.get("relations") or [])
                            stale += [
                                folder / old[k]
                                for k in (*KINDS, "overlay")
                                if old.get(k) and ASSET_RE.fullmatch(old[k])
                            ]
                        touched.append((cc, entry, before))

                # Relations may only point at clues that exist now, then the other ends are brought in line.
                for cc, entry, _ in touched:
                    rels = [
                        r
                        for r in entry.get("relations") or []
                        if (r["type"] != "group" or r["cc"] == cc)
                        and (r["cc"], r["id"]) != (cc, entry["id"])
                        and any(o.get("id") == r["id"] for o in items_of(r["cc"]))
                    ]
                    _set_rels(entry, rels)
                for cc, entry, before in touched:
                    _sync_relations(books, cc, entry, before, entry.get("relations") or [])

                # Overlay pictures and medals are rebuilt, never imported.
                for cc, entry, _ in touched:
                    folder = CLUES_DIR / cc
                    geo = next((k for k in GEOM_KINDS if entry.get(k)), None)
                    if entry["scope"] == "reg" and geo:
                        svg = _render_overlay(cc, folder / entry[geo], local)
                        if svg:
                            entry["overlay"] = _overlay_name(entry[geo])
                            _write_overlay(folder, entry["overlay"], svg)
                            written.append(folder / entry["overlay"])
                    _apply_medal(cc, entry, request, folder)

                for code, its in books.items():
                    _save(code, its)
                    _set_has(code, its)
            except Exception:
                for p in written:
                    p.unlink(missing_ok=True)
                raise
            for p in stale:
                p.unlink(missing_ok=True)

        listed = manifest.get("decks") if isinstance(manifest.get("decks"), list) else []
        deck_store.add([*listed, *(e.get("deck") for _, e, _ in touched)])
    return counts


@router.post("/clue-pack/import")
async def pack_import(request: Request):
    return await run_in_threadpool(_import, await request.body(), request)
