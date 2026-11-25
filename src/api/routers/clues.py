"""clues.py — Per-country clues: stage uploads (convert + simplify), save, edit, list, delete.

Layout:  data/clues/{CC}/clues.yaml  +  data/clues/{CC}/<nat|reg>_<tag>_#####.<ext>
Flow:    POST /clues/stage/{kind}  -> file is converted/simplified into data/clues/.staging, preview returned
         DELETE /clues/staged/{token} -> throws a staged file away (replaced / removed in the editor)
         POST /clues/{CC}          -> staged files are renamed into the country folder, entry added to yaml
         PUT  /clues/{CC}/{id}     -> same body; only the files that are sent get replaced (overwritten in place
                                      when the clue already has a file of that kind and the extension matches)
         POST /clues/{CC}/reorder  -> {ids: [...]} sets the order of the clues in the yaml
         GET  /clues/subcontinents -> subcontinents with the countries that have clues (for relating clues)
Relations: a clue has `relations: [{type, cc, id}]` (same_as | similar_to | group). They are two-way: saving or
         deleting a clue also updates the clue on the other end (even in another country's yaml).
         They also propagate when a link is ADDED (see _close_equivalence):
           same_as     is an equivalence: everything the clue is the same as is the same as each other.
           similar_to  is shared across a same_as class: if A is the same as B and A is similar to C, then B is
                       similar to C too (and to whatever C is the same as).
           group       stays inside one country and is transitive (see _close_group).
         Removing a link only removes that link.
         `relation_notes: {same_as|similar_to: {verb, text}}` is the sentence the card shows.
         `max_height`: px cap for the height of the clue image on the card, or of the map of a map-only clue.
Shades:  a polygons file may carry a `shade` column (0 = the main tone, 1..3 = lighter = rarer part of the region).
         The geo preview / GET /clues/{CC}/geo/{file} then adds a `shades` list next to the geometries.
Stats:   staging a geometry file also returns `stats` (see src/explore/country_clues/edit_mode/uploads/geospatial_files.py): the editor uses them to
         pre-fill the ratings of a new clue.
Overlay: a regional clue with a geometry file (polygons / lines / points) also gets `overlay`: a transparent SVG of
         the region drawn on its country (see src/explore/country_clues/regional_map_overlay.py), written next to the parquet as
         <same name>.overlay.svg every time the clue is saved. The card lays that picture over the photo instead of
         downloading the geometry and drawing it. The parquet stays the source of truth. If the geo layers aren't
         loaded when a clue is saved, no overlay is made and the card falls back to drawing the geometry; the
         script scripts/backfill_clue_overlays.py (re)builds them.
Needs:   Pillow, geopandas, shapely (>= 2.1 for gap-free coverage simplification), pyarrow, pyyaml.
"""

import contextlib
import csv
import io
import logging
import re
import shutil
import threading
import uuid
import zlib
from pathlib import Path
from typing import Literal

import geopandas as gpd
import pandas as pd
import pyarrow.parquet as pq
import shapely
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues import decks as deck_store
from src.explore.country_clues.clue_relations import (
    _relations_of,
    _set_rels,  # noqa: F401  (re-exported for clue_pack)
    _sync_relations,
)
from src.explore.country_clues.clues import (
    ASSET_RE,
    CLUES_DIR,
    GENERAL_TAGS,
    KINDS,
    LOCATION_TAGS,
    STAGING,
    TOKEN_RE,
    _apply_medal,
    _asset,
    _cc,
    _countries_with_clues,
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
    _summary,
    _write_overlay,
)
from src.explore.country_clues.edit_mode.uploads.csvs import _process_csv, _process_csv_areas
from src.explore.country_clues.edit_mode.uploads.geospatial_files import (
    PREVIEW_PRECISION,
    _process_geo,
    _shades_of,
    _to_geojson,
)
from src.explore.country_clues.edit_mode.uploads.images import _process_image
from src.explore.country_clues.regional_map_overlay import recolour, render_overlay
from src.explore.country_clues.regional_stain_areas import _borders, _outline  # noqa: PLC2701
from src.explore.country_clues.subcontinents import SUBCONTINENTS
from src.shared import prefs

logger = logging.getLogger("uvicorn.error")

AREA_KINDS = ("svg", "csv", "polygons", "lines", "points")  # what says which part of the country
GEOM_KINDS = ("polygons", "lines", "points")
STAGE_KINDS = ("image", "csv", "geometry")

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

_LOCK = threading.Lock()


router = APIRouter()


# ── overlay (the region pre-drawn on its country) ──────────────────────────


def _render_overlay(cc: str, geo_file: Path, local) -> str | None:
    """SVG text of a clue's geometry drawn on its country, or None when it can't be made
    (geo layers not loaded, unreadable file). Never raises: the card then draws the geometry itself.
    """
    if local is None:
        logger.warning("Clue overlay for %s skipped: the geo layers are not loaded yet", cc)
        return None
    try:
        gdf = gpd.read_parquet(geo_file)
        if "color" in gdf.columns:
            return None
        geoms = shapely.set_precision(gdf.geometry.to_numpy(), PREVIEW_PRECISION)
        shades = _shades_of(gdf)
        keep = ~shapely.is_empty(geoms)
        outline = _outline(local, cc)
        if outline is not None:
            keep &= shapely.dwithin(geoms, outline, 0.01)  # nothing far from the trimmed outline
        borders = _borders(local, cc) if outline is not None else None
        return render_overlay(outline, borders, [(geoms[keep], shades[keep])])
    except Exception:
        logger.exception("Could not draw the overlay of %s", geo_file.name)
        return None


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
            _, info = _process_geo(data, suffix, STAGING / f"{token}.parquet")
    except HTTPException:
        _discard(token)
        raise
    except Exception as e:
        _discard(token)
        raise HTTPException(422, f"Could not read {filename}: {e}") from e
    return {"token": token, "name": filename, **info}


# ── staging endpoints ──────────────────────────────────────────────────────


@router.post("/clues/stage/{kind}")
async def stage_upload(kind: str, filename: str, request: Request, cc: str | None = None):
    """Raw file bytes in the body (no multipart dependency)."""
    local = get_ctx(request).local_geo
    return await run_in_threadpool(
        _stage, kind, filename, await request.body(), _cc(cc) if cc else None, local
    )


class PathBody(BaseModel):
    path: str
    cc: str | None = None


@router.post("/clues/stage-path/{kind}")
async def stage_from_path(kind: str, body: PathBody, request: Request):
    """Same as above for a file already on this machine (the backend runs locally)."""
    p = Path(body.path.strip().strip('"'))
    if not p.is_file():
        raise HTTPException(404, f"No file at {p}")
    local = get_ctx(request).local_geo
    return await run_in_threadpool(
        _stage, kind, p.name, p.read_bytes(), _cc(body.cc) if body.cc else None, local, p.parent
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
                svg = _render_overlay(cc, folder / names[geo_kind], get_ctx(request).local_geo)
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
                svg = _render_overlay(cc, geo_file, get_ctx(request).local_geo)
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
