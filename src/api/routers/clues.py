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
import threading
import uuid
import zlib
from pathlib import Path

import geopandas as gpd
import pyarrow.parquet as pq
import shapely
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.explore.country_clues.clue_relations import (
    _set_rels,  # noqa: F401  (re-exported for clue_pack)
)
from src.explore.country_clues.clues import (
    CLUES_DIR,
    STAGING,
    TOKEN_RE,
    _asset,
    _cc,
    _countries_with_clues,
    _load,
    _purge_staging,
    _staged_path,
    _summary,
)
from src.explore.country_clues.edit_mode.uploads.csvs import _process_csv, _process_csv_areas
from src.explore.country_clues.edit_mode.uploads.geospatial_files import (
    PREVIEW_PRECISION,
    _process_geo,
    _shades_of,
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
