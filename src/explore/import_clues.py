"""import_clues.py — import a `.cluepack` file, replacing clues of the same country + id or adding them."""

import io
import json
import logging
import re
import zipfile
from pathlib import Path

import yaml
from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues import decks as deck_store
from src.explore.country_clues.clue_relations import _set_rels, _sync_relations
from src.explore.country_clues.clues import (
    _LOCK,
    ASSET_RE,
    CLUES_DIR,
    GENERAL_TAGS,
    GEOM_KINDS,
    KINDS,
    LOCATION_TAGS,
    _apply_medal,
    _cc,
    _load,
    _make_levels,
    _next_name,
    _next_num,
    _overlay_name,
    _save,
    _set_has,
    _valid,
    _write_overlay,
)
from src.explore.country_clues.country_projection import projection_for
from src.explore.country_clues.regional_map_overlay import _render_overlay
from src.explore.export_clues import FORMAT, KEEP

logger = logging.getLogger("uvicorn.error")
router = APIRouter()

ID_RE = re.compile(r"[\w-]{1,100}")
REL_TYPES = {"same_as", "similar_to", "group"}
LEGACY_TAGS = {"Topography": "Landscape"}  # tags of packs made before the rename


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
                            stale += [folder / n for n in (old.get("levels") or {}).values()]
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
                        svg = _render_overlay(cc, folder / entry[geo], local, projection_for(cc))
                        if svg:
                            entry["overlay"] = _overlay_name(entry[geo])
                            _write_overlay(folder, entry["overlay"], svg)
                            written.append(folder / entry["overlay"])
                    levels = _make_levels(folder, entry)
                    if levels:
                        entry["levels"] = levels
                        written += [folder / n for n in levels.values()]
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
