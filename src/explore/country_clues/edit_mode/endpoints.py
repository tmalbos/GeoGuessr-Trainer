"""endpoints.py — The routes that write a clue: stage an upload, add, edit, delete, reorder, depth, medal
preview, the geometry read the editor needs while drawing, the country base (outline + borders) and the
"Draw" region editor (units, fill, build).

The read routes live in src/explore/country_clues/endpoints.py.
"""

import contextlib
import json
import re
import shutil
import uuid
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import shapely
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues import decks as deck_store
from src.explore.country_clues.clue_relations import _relations_of, _sync_relations
from src.explore.country_clues.clues import (
    _LOCK,
    AREA_KINDS,
    ASSET_RE,
    CLUES_DIR,
    GENERAL_TAGS,
    GEOM_KINDS,
    KINDS,
    LOCATION_TAGS,
    STAGE_KINDS,
    STAGING,
    TOKEN_RE,
    _apply_medal,
    _asset,
    _cc,
    _load,
    _make_levels,
    _next_name,
    _next_num,
    _overlay_name,
    _purge_staging,
    _restandardize,
    _save,
    _set_has,
    _staged_path,
    _write_overlay,
    base,
)
from src.explore.country_clues.country_projection import projection_for
from src.explore.country_clues.edit_mode.draw.canvas import (
    Item,
    _canvas,
    _polygonal,
    _remember,
    _units,
)
from src.explore.country_clues.edit_mode.draw.tools import _build, _payload
from src.explore.country_clues.edit_mode.uploads.csvs import _process_csv, _process_csv_areas
from src.explore.country_clues.edit_mode.uploads.geospatial_files import (
    _process_geo,
    _shades_of,
    _to_geojson,
)
from src.explore.country_clues.edit_mode.uploads.images import _process_image
from src.explore.country_clues.regional_map_overlay import _render_overlay
from src.explore.country_clues.regional_stain_areas import _outline
from src.shared import prefs

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


router = APIRouter()


# ── staging ────────────────────────────────────────────────────────────────


def _discard(token: str) -> None:
    for p in STAGING.glob(f"{token}.*"):
        p.unlink(missing_ok=True)


def _stage(
    kind: str,
    filename: str,
    data: bytes,
    cc: str | None = None,
    local=None,
    base_dir: Path | None = None,
    named: bool = False,
) -> dict:
    if kind not in STAGE_KINDS:
        raise HTTPException(404, "Unknown file kind")
    if not data:
        raise HTTPException(422, "Empty file")
    STAGING.mkdir(parents=True, exist_ok=True)
    _purge_staging()
    token = uuid.uuid4().hex
    suffix = Path(filename).suffix.lower()
    try:
        if kind == "image":
            info = _process_image(data, STAGING / f"{token}.image.webp")
        elif kind == "csv":
            if cc:
                info = _process_csv_areas(
                    data, cc, local, base_dir, STAGING / f"{token}.polygons.parquet"
                )
            else:
                info = _process_csv(data, STAGING / f"{token}.csv.csv")
        else:
            outline = _outline(local, cc) if named and cc and local is not None else None
            _, info = _process_geo(data, suffix, STAGING / f"{token}.parquet", outline, named)
    except HTTPException:
        _discard(token)
        raise
    except Exception as e:
        _discard(token)
        raise HTTPException(422, f"Could not read {filename}: {e}") from e
    return {"token": token, "name": filename, **info}


@router.post("/clues/stage/{kind}")
async def stage_upload(
    kind: str, filename: str, request: Request, cc: str | None = None, named: bool = False
):
    """Raw file bytes in the body (no multipart dependency)."""
    local = get_ctx(request).local_geo
    return await run_in_threadpool(
        _stage, kind, filename, await request.body(), _cc(cc) if cc else None, local, None, named
    )


class PathBody(BaseModel):
    path: str
    cc: str | None = None
    named: bool = False


@router.post("/clues/stage-path/{kind}")
async def stage_from_path(kind: str, body: PathBody, request: Request):
    """Same as above for a file already on this machine (the backend runs locally)."""
    p = Path(body.path.strip().strip('"'))
    if not p.is_file():
        raise HTTPException(404, f"No file at {p}")
    local = get_ctx(request).local_geo
    return await run_in_threadpool(
        _stage,
        kind,
        p.name,
        p.read_bytes(),
        _cc(body.cc) if body.cc else None,
        local,
        p.parent,
        body.named,
    )


@router.get("/clues/staged/{token}/{kind}")
def staged_file(token: str, kind: str):
    p = _staged_path(token, kind)
    # An SVG has to be served as such for the editor to preview it.
    return FileResponse(p, media_type="image/svg+xml" if p.suffix == ".svg" else None)


# Must stay ABOVE DELETE /clues/{cc}/{clue_id}, or "staged" is read as a country code.
@router.delete("/clues/staged/{token}")
def discard_staged(token: str):
    """Throw away a staged upload that was replaced or removed in the editor."""
    if not TOKEN_RE.fullmatch(token):
        raise HTTPException(422, "Invalid staged file")
    _discard(token)
    return {"ok": True}


# ── clues ──────────────────────────────────────────────────────────────────


class RelationIn(BaseModel):
    type: Literal["same_as", "similar_to", "group"]
    cc: str
    id: str


class NoteIn(BaseModel):
    verb: Literal["has", "uses"] = "uses"
    text: str = ""


class ViewIn(BaseModel):
    """The zoom / pan a drawn regional clue opens on (same meaning as in ZoomableRegion.jsx): a window of 1/k of
    the map centred on (fx, fy), both as fractions of the map.
    """

    k: float = Field(ge=1, le=80)
    kh: float | None = Field(
        None, ge=1, le=80
    )  # vertical zoom when the window isn't in the map's proportions (None = same as k)
    fx: float = Field(ge=0, le=1)
    fy: float = Field(ge=0, le=1)


class ClueBody(BaseModel):
    scope: Literal["nat", "reg"]
    info: str
    tags: list[str]
    frequency: int | None = Field(None, ge=1, le=10)
    ease: int | None = Field(None, ge=1, le=10)
    reliability: int | None = Field(None, ge=1, le=10)
    blur: bool = False  # blur the clue image until hovered (only kept when the clue has an image)
    max_height: int | None = Field(
        None, ge=100, le=2000
    )  # px cap for the height of the image (or of a map-only clue's map) on the card
    show_names: bool = True  # drawn regional clue: print the area names on the map where they fit (False = hide them)
    view: ViewIn | None = (
        None  # drawn regional clue: zoom / pan the card opens on (None = the whole country)
    )
    prefix_colors: bool = (
        False  # drawn regional clue: colour the areas by the start of their names (area codes...)
    )
    source: str = ""  # link to where the information came from (optional)
    relations: list[
        RelationIn
    ] = []  # the full list of relations of this clue (replaces the stored one)
    relation_notes: dict[str, NoteIn] = {}  # sentence for same_as / similar_to
    files: dict[str, str] = {}  # kind -> staging token
    remove: list[str] = []  # kinds of the clue's own files to delete (e.g. the media changed type)
    deck: str = ""  # deck (group) the clue belongs to; empty = the last one used


def _validate(b: ClueBody, have: set[str] | frozenset[str] = frozenset()) -> tuple[str, str]:
    """`have` = file kinds the clue already stores (when editing), which count as present."""
    unknown = [t for t in b.tags if t not in LOCATION_TAGS + GENERAL_TAGS]
    if unknown:
        raise HTTPException(422, f"Unknown tags: {', '.join(unknown)}")
    loc = [t for t in b.tags if t in LOCATION_TAGS]
    gen = [t for t in b.tags if t in GENERAL_TAGS]
    if len(loc) != 1:
        raise HTTPException(422, "Pick exactly one of Urban / Rural / Anywhere")
    if len(gen) != 1:
        raise HTTPException(422, "Pick exactly one category tag")
    if not b.info.strip():
        raise HTTPException(422, "Info is empty")
    allowed = {"image"} if b.scope == "nat" else set(KINDS)
    extra = set(b.files) - allowed
    if extra:
        raise HTTPException(422, f"{', '.join(sorted(extra))} not allowed in a {b.scope} clue")
    kinds = (set(b.files) | set(have)) - set(b.remove)
    if b.scope == "nat" and "image" not in kinds:
        raise HTTPException(422, "A national clue needs an image")
    if b.scope == "reg" and not any(k in kinds for k in AREA_KINDS):
        msg = "A regional clue needs a region: an SVG map, a CSV or a geometry file"
        raise HTTPException(422, msg)
    return loc[0], gen[0]


def _clean_source(b: ClueBody) -> str | None:
    """The source link, or None when empty. Only http(s) links are accepted."""
    url = b.source.strip()
    if not url:
        return None
    if len(url) > 500 or not URL_RE.fullmatch(url):
        raise HTTPException(422, "The source must be a link starting with http:// or https://")
    return url


@router.get("/clues/{cc}/geo/{name}")
def clue_geo(cc: str, name: str):
    gdf = gpd.read_parquet(_asset(cc, name))
    named = (
        {"names": gdf["name"].to_numpy(), "colors": gdf["color"].to_numpy()}
        if {"name", "color"} <= set(gdf.columns)
        else {}
    )
    if named and {"extra_blur", "extra_max_height"} <= set(gdf.columns):
        named["blurs"] = gdf["extra_blur"].fillna(False).astype(bool).to_numpy()
        named["heights"] = (
            pd.to_numeric(gdf["extra_max_height"], errors="coerce").fillna(0).astype(int).to_numpy()
        )
    if named and "extra" in gdf.columns:
        named["extras"] = gdf["extra"].fillna("").astype(str).to_numpy()
    return _to_geojson(gdf.geometry.to_numpy(), shades=_shades_of(gdf), precision=None, **named)


@router.post("/clues/{cc}", status_code=201)
def add_clue(cc: str, body: ClueBody, request: Request):
    cc = _cc(cc)
    location, general = _validate(body)
    source = _clean_source(body)
    rels, notes = _relations_of(body, cc)
    folder = CLUES_DIR / cc
    deck = deck_store.use(body.deck)
    with _LOCK:
        staged = {k: _staged_path(t, k) for k, t in body.files.items()}
        if "polygons" in staged:
            _restandardize(staged["polygons"])
        folder.mkdir(parents=True, exist_ok=True)
        names: dict[str, str] = {}
        made: list[str] = []  # overlay files written by this request
        try:
            num = _next_num(folder, body.scope, general)  # image, region and overlay share it
            for kind in KINDS:
                if kind in staged:
                    name = _next_name(folder, body.scope, general, staged[kind].suffix, num)
                    shutil.move(staged[kind], folder / name)
                    names[kind] = name
            entry = {
                "id": deck_store.new_clue_id(deck),
                "scope": body.scope,
                "info": body.info.strip(),
                "tags": [location, general],
                "frequency": body.frequency,
                "ease": body.ease,
                "reliability": body.reliability,
                "blur": True if body.blur else None,
                "max_height": body.max_height,
                "source": source,
                "relations": rels or None,
                "relation_notes": notes or None,
                "deck": deck,
                "show_names": None if body.show_names else False,
                "view": body.view.model_dump() if body.view and body.scope == "reg" else None,
                "prefix_colors": True if body.prefix_colors and body.scope == "reg" else None,
                **names,
            }
            entry = {k: v for k, v in entry.items() if v is not None}
            if "image" not in entry:
                entry.pop("blur", None)
            geo_kind = next((k for k in GEOM_KINDS if k in names), None)
            if body.scope == "reg" and geo_kind:
                svg = _render_overlay(
                    cc, folder / names[geo_kind], get_ctx(request).local_geo, projection_for(cc)
                )
                if svg:
                    entry["overlay"] = _overlay_name(names[geo_kind])
                    _write_overlay(folder, entry["overlay"], svg)
                    made.append(entry["overlay"])
            levels = _make_levels(folder, entry)
            if levels:
                entry["levels"] = levels
                made.extend(levels.values())
            _apply_medal(cc, entry, request, folder)
            items = [*_load(cc), entry]
            books = {cc: items}
            _sync_relations(books, cc, entry, [], rels)
            for code, its in books.items():
                _save(code, its)
            _set_has(cc, items)
        except Exception:
            for name in [*names.values(), *made]:
                (folder / name).unlink(missing_ok=True)
            raise
    return entry


class OrderBody(BaseModel):
    ids: list[str]


@router.post("/clues/{cc}/reorder")
def reorder_clues(cc: str, body: OrderBody):
    """Put the clues in the given order. Ids that don't exist are ignored; clues not mentioned keep
    their relative order and go at the end, so a stale list from the browser never loses a clue.
    """
    cc = _cc(cc)
    with _LOCK:
        items = _load(cc)
        by_id = {c.get("id"): c for c in items}
        ordered = [by_id[i] for i in dict.fromkeys(body.ids) if i in by_id]
        placed = {c.get("id") for c in ordered}
        ordered += [c for c in items if c.get("id") not in placed]
        _save(cc, ordered)
    return {"ok": True}


class DepthBody(BaseModel):
    depth: int = Field(ge=1, le=20)


@router.put("/clues/{cc}/{clue_id}/depth")
def set_clue_depth(cc: str, clue_id: str, body: DepthBody):
    """Remember how many characters of the names the user chose to see on a prefix-coloured clue."""
    cc = _cc(cc)
    with _LOCK:
        depths = dict(prefs.load().get("clue_depths") or {})
        depths[f"{cc}/{clue_id}"] = body.depth
        prefs.update(clue_depths=depths)
    return {"ok": True}


class MedalBody(ClueBody):
    clue_id: str | None = None  # the clue being edited, when there is one


@router.post("/clues/{cc}/medal")
def preview_medal(cc: str, body: MedalBody, request: Request):
    """The score and medal this clue would get if it were saved now. Nothing is kept."""
    cc = _cc(cc)
    folder = CLUES_DIR / cc
    old = (
        next((c for c in _load(cc) if c.get("id") == body.clue_id), None) if body.clue_id else None
    )
    replaced = set(body.files) | set(body.remove)
    if replaced & set(GEOM_KINDS):
        replaced |= set(GEOM_KINDS)
    entry = {
        "id": body.clue_id or "preview",
        "scope": body.scope,
        "tags": body.tags,
        "frequency": body.frequency,
        "ease": body.ease,
        "reliability": body.reliability,
        "relations": [r.model_dump() for r in body.relations] or None,
        **{k: old[k] for k in KINDS if old and old.get(k) and k not in replaced},
    }
    made_folder = not folder.exists()
    folder.mkdir(parents=True, exist_ok=True)
    copies: list[Path] = []
    try:
        for kind, token in body.files.items():
            if kind == "image":
                continue
            src = _staged_path(token, kind)
            name = f"preview_{token}{src.suffix}"
            shutil.copy2(src, folder / name)
            copies.append(folder / name)
            entry[kind] = name
        entry = {k: v for k, v in entry.items() if v is not None}
        _apply_medal(cc, entry, request, folder)
    finally:
        for p in copies:
            p.unlink(missing_ok=True)
        if made_folder:
            with contextlib.suppress(OSError):
                folder.rmdir()
    return {"score": entry.get("score"), "medal": entry.get("medal")}


@router.put("/clues/{cc}/{clue_id}")
def update_clue(cc: str, clue_id: str, body: ClueBody, request: Request):
    """Edit a clue. Text, tags, ratings, source and relations are replaced; files only when new ones are sent.

    A new file of a kind the clue already has (same extension) overwrites the old file in place, so
    nothing is left behind. A new geometry file replaces whichever geometry the clue had: if it is the
    same geometry kind it is overwritten in place, otherwise the old geometry file is deleted.
    The overlay picture of a regional clue is redrawn on every save.
    """
    cc = _cc(cc)
    source = _clean_source(body)
    rels, notes = _relations_of(body, cc)
    folder = CLUES_DIR / cc
    with _LOCK:
        items = _load(cc)
        old = next((c for c in items if c.get("id") == clue_id), None)
        if old is None:
            raise HTTPException(404, "Clue not found")
        if old.get("scope") != body.scope:
            raise HTTPException(422, "A clue can't switch between national and regional")
        location, general = _validate(body, {k for k in KINDS if old.get(k)})
        # File names carry the category tag (reg_roads_00003.webp): a file with another prefix is stale.
        prefix = f"{body.scope}_{''.join(general.split()).lower()}_"
        num = _next_num(folder, body.scope, general)  # one number for every new file of this clue
        staged = {k: _staged_path(t, k) for k, t in body.files.items()}
        replaced = set(staged) | set(body.remove)
        if replaced & set(GEOM_KINDS):
            replaced |= set(GEOM_KINDS)
        target = staged.get("polygons")
        if target is None and old.get("polygons") and "polygons" not in replaced:
            target = folder / old["polygons"]
        if target is not None:
            _restandardize(target)
        names: dict[str, str] = {}
        overwrite: dict[
            str, Path
        ] = {}  # kind -> staged file that goes over the clue's existing file
        fresh_overlay: str | None = (
            None  # overlay written by this request under a name the clue didn't have
        )
        fresh_levels: list[str] = []  # per-depth maps written by this request
        try:
            for kind in KINDS:
                if kind not in staged:
                    continue
                old_name = old.get(kind)
                if (
                    old_name
                    and ASSET_RE.fullmatch(old_name)
                    and old_name.startswith(prefix)
                    and Path(old_name).suffix == staged[kind].suffix
                    and (folder / old_name).is_file()
                ):
                    # Same slot: keep the file name, swap the content after the yaml is saved.
                    names[kind] = old_name
                    overwrite[kind] = staged[kind]
                    continue
                name = _next_name(folder, body.scope, general, staged[kind].suffix, num)
                shutil.move(staged[kind], folder / name)
                names[kind] = name
            # Files the clue keeps but whose name carries an old category: copy them under the right name
            # (the old ones are deleted after the yaml is saved, so a failure loses nothing).
            renamed: list[str] = []
            for kind in KINDS:
                old_name = old.get(kind)
                if (
                    kind in staged
                    or kind in replaced
                    or not old_name
                    or old_name.startswith(prefix)
                    or not ASSET_RE.fullmatch(old_name)
                    or not (folder / old_name).is_file()
                ):
                    continue
                name = _next_name(folder, body.scope, general, Path(old_name).suffix, num)
                shutil.copy2(folder / old_name, folder / name)
                names[kind] = name
                renamed.append(kind)
            kept = {k: old[k] for k in KINDS if old.get(k) and k not in replaced}
            entry = {
                "id": old["id"],
                "scope": old["scope"],
                "info": body.info.strip(),
                "tags": [location, general],
                "frequency": body.frequency,
                "ease": body.ease,
                "reliability": body.reliability,
                "blur": True if body.blur else None,
                "max_height": body.max_height,
                "source": source,
                "relations": rels or None,
                "relation_notes": notes or None,
                "deck": deck_store.use(body.deck) if body.deck.strip() else old.get("deck"),
                "show_names": None if body.show_names else False,
                "view": body.view.model_dump() if body.view and body.scope == "reg" else None,
                "prefix_colors": True if body.prefix_colors and body.scope == "reg" else None,
                **kept,
                **names,
            }
            entry = {k: v for k, v in entry.items() if v is not None}
            if "image" not in entry:
                entry.pop("blur", None)
            geo_kind = next((k for k in GEOM_KINDS if entry.get(k)), None)
            if entry["scope"] == "reg" and geo_kind:
                # A geometry file that is being overwritten is still the staged one at this point.
                geo_file = overwrite.get(geo_kind, folder / entry[geo_kind])
                svg = _render_overlay(cc, geo_file, get_ctx(request).local_geo, projection_for(cc))
                if svg:
                    entry["overlay"] = _overlay_name(entry[geo_kind])
                    _write_overlay(folder, entry["overlay"], svg)
                    if entry["overlay"] != old.get("overlay"):
                        fresh_overlay = entry["overlay"]
                elif old.get("overlay") and geo_kind not in staged:
                    # Same geometry, can't redraw now: keep the picture (under the geometry's new name, if it has one).
                    want = _overlay_name(entry[geo_kind])
                    if want != old["overlay"] and (folder / old["overlay"]).is_file():
                        shutil.copy2(folder / old["overlay"], folder / want)
                        fresh_overlay = want
                        entry["overlay"] = want
                    else:
                        entry["overlay"] = old["overlay"]
            levels = _make_levels(folder, entry, overwrite.get("polygons"))
            if levels:
                entry["levels"] = levels
                fresh_levels = list(levels.values())
            # files being overwritten are still staged at this point: score from those
            _apply_medal(cc, entry, request, folder, {names[k]: s for k, s in overwrite.items()})
            updated = [entry if c is old else c for c in items]
            books = {cc: updated}
            _sync_relations(books, cc, entry, old.get("relations") or [], rels)
            for code, its in books.items():
                _save(code, its)
            _set_has(cc, updated)
        except Exception:
            # Only brand-new files are removed; overwrite targets are the clue's existing files.
            for kind, name in names.items():
                if kind not in overwrite:
                    (folder / name).unlink(missing_ok=True)
            if fresh_overlay:
                (folder / fresh_overlay).unlink(missing_ok=True)
            for name in fresh_levels:
                if name not in (old.get("levels") or {}).values():
                    (folder / name).unlink(missing_ok=True)
            raise
        for kind, src in overwrite.items():
            try:
                src.replace(folder / names[kind])
            except OSError as e:
                msg = "Could not overwrite the old file. Close anything using it and try again."
                raise HTTPException(500, msg) from e
        # Old files that no longer belong to the clue (replaced by a differently named file).
        kept_names = set(names.values())
        for kind in replaced | set(renamed):
            old_name = old.get(kind)
            if old_name and old_name not in kept_names and ASSET_RE.fullmatch(old_name):
                (folder / old_name).unlink(missing_ok=True)
        old_overlay = old.get("overlay")
        if old_overlay and old_overlay != entry.get("overlay") and ASSET_RE.fullmatch(old_overlay):
            (folder / old_overlay).unlink(missing_ok=True)
        for name in (old.get("levels") or {}).values():
            if name not in (entry.get("levels") or {}).values() and ASSET_RE.fullmatch(name):
                (folder / name).unlink(missing_ok=True)
    return entry


@router.delete("/clues/{cc}/{clue_id}")
def delete_clue(cc: str, clue_id: str):
    cc = _cc(cc)
    with _LOCK:
        items = _load(cc)
        gone = next((c for c in items if c.get("id") == clue_id), None)
        if gone is None:
            raise HTTPException(404, "Clue not found")
        remaining = [c for c in items if c is not gone]
        books = {cc: remaining}
        # Clues that pointed at this one lose the relation (and the sentence, if it was their last of that type).
        _sync_relations(books, cc, gone, gone.get("relations") or [], [])
        for code, its in books.items():
            _save(code, its)
        _set_has(cc, remaining)
        depths = dict(prefs.load().get("clue_depths") or {})
        if depths.pop(f"{cc}/{clue_id}", None) is not None:
            prefs.update(clue_depths=depths)
        for name in (gone.get("levels") or {}).values():
            if ASSET_RE.fullmatch(name):
                (CLUES_DIR / cc / name).unlink(missing_ok=True)
        for kind in (*KINDS, "overlay"):
            if gone.get(kind) and ASSET_RE.fullmatch(gone[kind]):
                (CLUES_DIR / cc / gone[kind]).unlink(missing_ok=True)
    return {"ok": True}


# ── country base ───────────────────────────────────────────────────────────
# Country outline + first-level borders, so a regional clue is drawn ON its country.
#
# GET /clues/{cc}/base -> {outline, borders}, both lon/lat GeoJSON (or null when the local geo layers
# have nothing for that country). Cached in memory for the life of the server.
#
# Both come from regional_stain_areas._outline / regional_stain_areas._borders, which are already simplified TOGETHER (src/geo/simplify.py,
# resolution based). Nothing is simplified again here: a second, fixed simplification is what made small
# countries look heavily simplified and let the borders drift away from the outline.


@router.get("/clues/{cc}/base")
async def clue_base(cc: str, request: Request):
    if not re.fullmatch(r"[A-Za-z]{2}", cc):
        raise HTTPException(422, "Invalid country code")
    local = get_ctx(request).local_geo
    return await run_in_threadpool(base, cc, local)


# ── draw editor ────────────────────────────────────────────────────────────
# Backing for the "Draw" region editor of a regional clue.
#
# GET  /clues/{cc}/draw/units?level=N  -> {levels, units: [{id, name, geometry}]}. Level 0 is the whole country;
#                                         1..levels are the local admin layer's levels (state, subregion, city).
# POST /clues/{cc}/draw/build          -> GeoJSON FeatureCollection, one colored feature per category name, built from
#                                         the drawing's items in order: fills of a subdivision and freeform strokes
#                                         (clipped to the country outline). A later item wins over an earlier one.
#
# The canvas and the build live in src/explore/country_clues/edit_mode/draw/ (canvas.py, tools.py).


def _local(request: Request):
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    return local


@router.get("/clues/{cc}/draw/units")
async def draw_units(cc: str, request: Request, level: int = 0):
    return await run_in_threadpool(_payload, _local(request), _cc(cc), level)


class BuildBody(BaseModel):
    items: list[Item]
    colored: bool = True  # False = every area keeps the pale land color


class FillBody(BaseModel):
    items: list[Item]
    level: int = 0
    id: int = 0
    point: list[float]  # lon, lat of the click
    erase: bool = False  # Shift + eraser: only report the painted part under the click (None when there is none)
    whole: bool = False  # Shift + fill: every separate piece of the division (islands too), not just the one under the click


def _fill(local, cc: str, body: FillBody) -> dict:
    """Bucket fill inside one division. Clicked on empty space: the connected empty area around the click.
    Clicked on a painted area: the connected part of that painted area. Never more than the division.
    """
    units = _units(local, cc, body.level)
    if not 0 <= body.id < len(units) or len(body.point) != 2:
        raise HTTPException(422, "Nothing to fill there.")
    unit = units[body.id][1]
    pt = shapely.Point(body.point)
    canvas = _canvas(local, cc, body.items)
    cat = canvas.at(pt)
    if cat is not None and body.whole:
        # Shift on a painted stain: that stain plus every unpainted gap of the division. Other stains stay as they are.
        others = canvas.near(unit.bounds, cat, other=True)
        area = shapely.difference(unit, shapely.union_all(others)) if len(others) else unit
    elif cat is not None:
        area = shapely.intersection(unit, shapely.union_all(canvas.near(unit.bounds, cat)))
    elif body.erase:
        return {"geometry": None}
    else:
        near = canvas.near(unit.bounds)
        area = shapely.difference(unit, shapely.union_all(near)) if len(near) else unit
    area = _polygonal(area)
    parts = [] if area is None else [p for p in shapely.get_parts(area) if p.geom_type == "Polygon"]
    if not parts:
        raise HTTPException(422, "Nothing to fill there.")
    part = (
        shapely.union_all(parts) if body.whole else next((p for p in parts if p.covers(pt)), None)
    )
    if part is None:
        part = min(parts, key=lambda p: p.distance(pt))
    gid = uuid.uuid4().hex
    _remember(gid, part)  # the browser sends this shape back by gid, not by geometry
    return {"geometry": json.loads(shapely.to_geojson(part)), "gid": gid}


@router.post("/clues/{cc}/draw/fill")
async def draw_fill(cc: str, body: FillBody, request: Request):
    return await run_in_threadpool(_fill, _local(request), _cc(cc), body)


@router.post("/clues/{cc}/draw/build")
async def draw_build(cc: str, body: BuildBody, request: Request):
    return await run_in_threadpool(_build, _local(request), _cc(cc), body.items, body.colored)
