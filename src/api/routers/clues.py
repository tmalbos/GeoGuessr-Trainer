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
         `relation_notes: {same_as|similar_to: {verb, text}}` is the sentence the card shows.
         `badge`: "subcontinent" | "world" (manual uniqueness badge).
         `max_height`: px cap for the clue image on the card (only kept when the clue has an image).
Shades:  a polygons file may carry a `shade` column (0 = the main tone, 1..4 = lighter = rarer part of the region).
         The geo preview / GET /clues/{CC}/geo/{file} then adds a `shades` list next to the geometries.
Needs:   Pillow, geopandas, shapely (>= 2.1 for gap-free coverage simplification), pyarrow, pyyaml.
"""

import csv
import io
import json
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Annotated, Literal
from xml.etree import ElementTree as ET

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely
import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.geo.subcontinents import SUBCONTINENTS

CLUES_DIR = Path(__file__).parents[3] / "data" / "clues"
STAGING = CLUES_DIR / ".staging"

# Keep in sync with frontend/src/pages/Explore/clues/tags.js
LOCATION_TAGS = ["Urban", "Rural", "Anywhere"]
GENERAL_TAGS = [
    "Architecture", "Phone Numbers", "Postal Codes", "Topography", "Vegetation",
    "Infrastructure", "Vehicle", "License Plate", "Flags", "Roads", "Subdivision Names",
    "City Names", "Agriculture", "Soil", "Brand", "Language", "Script", "Road Sign",
    "Electricity Pole", "Bollard", "National Parks", "Biome", "Religion",
]  # fmt: skip

KINDS = ("image", "svg", "csv", "polygons", "lines", "points")
AREA_KINDS = ("svg", "csv", "polygons", "lines", "points")  # what says which part of the country
GEOM_KINDS = ("polygons", "lines", "points")
GEOM_KIND = {"Polygon": "polygons", "LineString": "lines", "Point": "points"}
STAGE_KINDS = ("image", "svg", "csv", "geometry")

NOTE_TYPES = ("same_as", "similar_to")  # relations that carry a sentence

SIMPLIFY_TOL_DEG = 0.0005  # ~55 m. Lower = more faithful, bigger files.
PREVIEW_PRECISION = 1e-5  # ~1 m, only for the GeoJSON sent to the browser
IMAGE_MAX_PX = 1600
IMAGE_QUALITY = 88
PREVIEW_MAX_FEATURES = 5000
MAX_SHADE = 4

URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)

ASSET_RE = re.compile(r"(nat|reg)_[a-z]+_\d{5}\.(webp|svg|csv|parquet)")
TOKEN_RE = re.compile(r"[0-9a-f]{32}")

# Country code -> subcontinent label ("CL" -> "South America"), for the badge tooltip.
_SUB_OF = {code: label for _, label, codes in SUBCONTINENTS for code in codes.split()}
_LOCK = threading.Lock()

# Countries that have at least one clue shown in the normal view. Built from disk on first request,
# then kept in memory for the life of the server (never written to disk) and updated by each change.
_SUM_LOCK = threading.Lock()
_HAS: set[str] | None = None
_SHOWN = {"visible", "guide-only"}


def _has_shown(items: list[dict]) -> bool:
    return any(c.get("visibility", "visible") in _SHOWN for c in items)


def _summary() -> set[str]:
    global _HAS  # noqa: PLW0603
    with _SUM_LOCK:
        if _HAS is None:
            _HAS = {
                d.name.upper()
                for d in sorted(CLUES_DIR.glob("??"))
                if d.is_dir() and re.fullmatch(r"[A-Za-z]{2}", d.name) and _has_shown(_load(d.name))
            }
        return set(_HAS)


def _set_has(cc: str, items: list[dict]) -> None:
    """Called after a country's clues change: only that country's entry is touched, nothing is rescanned."""
    with _SUM_LOCK:
        if _HAS is None:  # not built yet; the first request will read the final state from disk
            return
        (_HAS.add if _has_shown(items) else _HAS.discard)(cc)


router = APIRouter()


# ── helpers ────────────────────────────────────────────────────────────────


def _cc(code: str) -> str:
    if not re.fullmatch(r"[A-Za-z]{2}", code):
        raise HTTPException(422, "Invalid country code")
    return code.upper()


def _yaml_path(cc: str) -> Path:
    return CLUES_DIR / cc / "clues.yaml"


def _load(cc: str) -> list[dict]:
    p = _yaml_path(cc)
    if not p.is_file():
        return []
    try:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or []
    except yaml.YAMLError as e:
        raise HTTPException(500, f"{p.name} for {cc} is corrupted") from e


def _save(cc: str, items: list[dict]) -> None:
    p = _yaml_path(cc)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(
        yaml.safe_dump(items, allow_unicode=True, sort_keys=False, width=100), encoding="utf-8"
    )
    tmp.replace(p)


def _asset(cc: str, name: str) -> Path:
    if not ASSET_RE.fullmatch(name):
        raise HTTPException(422, "Invalid file name")
    p = CLUES_DIR / _cc(cc) / name
    if not p.is_file():
        raise HTTPException(404, "File not found")
    return p


def _next_name(folder: Path, scope: str, general: str, suffix: str) -> str:
    key = "".join(general.split()).lower()
    prefix = f"{scope}_{key}_"
    pat = re.compile(re.escape(prefix) + r"(\d{5})\..+")
    nums = [int(m.group(1)) for p in folder.glob(prefix + "*") if (m := pat.fullmatch(p.name))]
    return f"{prefix}{max(nums, default=-1) + 1:05d}{suffix}"


def _staged_path(token: str, kind: str) -> Path:
    if not TOKEN_RE.fullmatch(token) or kind not in KINDS:
        raise HTTPException(422, "Invalid staged file")
    hits = list(STAGING.glob(f"{token}.{kind}.*"))
    if not hits:
        raise HTTPException(410, f"The uploaded {kind} file expired. Load it again.")
    return hits[0]


def _purge_staging() -> None:
    cutoff = time.time() - 86400
    for p in STAGING.glob("*"):
        if p.is_file() and p.stat().st_mtime < cutoff:
            p.unlink(missing_ok=True)


def _vertices(geoms) -> int:
    return int(shapely.get_num_coordinates(geoms).sum())


def _shades_of(gdf: gpd.GeoDataFrame) -> np.ndarray:
    """The `shade` column of a geometry file as ints 0..MAX_SHADE (zeros when it has none)."""
    if "shade" not in gdf.columns:
        return np.zeros(len(gdf), dtype=int)
    col = pd.to_numeric(gdf["shade"], errors="coerce").fillna(0)
    return col.astype(int).clip(0, MAX_SHADE).to_numpy()


def _to_geojson(geoms, limit: int | None = None, shades: np.ndarray | None = None) -> dict:
    """GeometryCollection for the browser. When `shades` (one per geometry) has anything above 0 it is sent
    along as `shades`, in the same order as `geometries`.
    """
    if limit and len(geoms) > limit:
        step = -(-len(geoms) // limit)
        geoms = geoms[::step]
        if shades is not None:
            shades = shades[::step]
    g = shapely.set_precision(geoms, PREVIEW_PRECISION)
    keep = ~shapely.is_empty(g)
    g = g[keep]
    out = {
        "type": "GeometryCollection",
        "geometries": [json.loads(s) for s in shapely.to_geojson(g)],
    }
    if shades is not None and shades[keep].any():
        out["shades"] = [int(v) for v in shades[keep]]
    return out


# ── converters (run in a thread) ───────────────────────────────────────────


def _process_image(data: bytes, out: Path) -> dict:
    img = Image.open(io.BytesIO(data))
    img.load()
    img = ImageOps.exif_transpose(img)
    if max(img.size) > IMAGE_MAX_PX:
        img.thumbnail((IMAGE_MAX_PX, IMAGE_MAX_PX), Image.LANCZOS)
    has_alpha = "A" in img.getbands() or "transparency" in img.info
    img = img.convert("RGBA" if has_alpha else "RGB")
    img.save(out, "WEBP", quality=IMAGE_QUALITY, method=6)
    return {
        "width": img.width,
        "height": img.height,
        "bytes_in": len(data),
        "bytes_out": out.stat().st_size,
    }


def _process_svg(data: bytes, out: Path) -> dict:
    """Keep the vector as it is; only check that it is an SVG with the expected layers."""
    text = data.decode("utf-8-sig", errors="replace")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        raise HTTPException(422, f"Could not read the SVG: {e}") from e
    if not root.tag.lower().endswith("svg"):
        raise HTTPException(422, "That file is not an SVG")
    if not any(el.get("id") == "Background" for el in root.iter()):
        raise HTTPException(422, 'The SVG needs a layer with id="Background"')
    out.write_text(text, encoding="utf-8")
    return {"bytes_in": len(data), "bytes_out": out.stat().st_size}


def _process_csv(data: bytes, out: Path) -> dict:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = [
        [c.strip() for c in r]
        for r in csv.reader(io.StringIO(text), dialect)
        if any(c.strip() for c in r)
    ]
    if len(rows) < 2 or len(rows[0]) < 2:
        raise HTTPException(422, "CSV needs a header (GroupName,Level1,...) and at least one row")
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    out.write_text(buf.getvalue(), encoding="utf-8")
    return {
        "rows": rows[:200],
        "row_count": len(rows) - 1,
        "bytes_in": len(data),
        "bytes_out": out.stat().st_size,
    }


def _read_geo(data: bytes, suffix: str) -> gpd.GeoDataFrame:
    tmp = STAGING / f"{uuid.uuid4().hex}.upload{suffix}"
    tmp.write_bytes(data)
    try:
        if suffix == ".parquet":
            return gpd.read_parquet(tmp)
        if suffix == ".zip":
            return gpd.read_file(f"zip://{tmp}")
        if suffix == ".shp":
            msg = "A lone .shp is not enough. Zip the .shp, .shx, .dbf and .prj together."
            raise HTTPException(422, msg)
        return gpd.read_file(tmp)
    finally:
        tmp.unlink(missing_ok=True)


def _simplify_polygons(parts: np.ndarray) -> np.ndarray:
    """Coverage simplification keeps shared borders identical, so neighbours leave no gaps/slivers.
    Falls back to per-polygon topology-preserving simplification (older shapely, overlapping input).
    """
    try:
        if not shapely.coverage_is_valid(parts).all():
            msg = "not a clean coverage"
            raise ValueError(msg)  # noqa: TRY301
        out = shapely.coverage_simplify(parts, SIMPLIFY_TOL_DEG)
    except Exception:  # noqa: BLE001
        out = shapely.simplify(parts, SIMPLIFY_TOL_DEG, preserve_topology=True)
    # Never lose a feature: if simplifying emptied or broke one, keep the original.
    bad = shapely.is_empty(out) | ~shapely.is_valid(out)
    return np.where(bad, parts, out)


def _process_geo(data: bytes, suffix: str, token: str) -> tuple[str, dict]:
    gdf = _read_geo(data, suffix)
    gdf = gdf.set_crs(4326) if gdf.crs is None else gdf.to_crs(4326)
    arr = gdf.geometry.to_numpy()
    present = ~shapely.is_missing(arr)
    arr = arr[present]
    shades = _shades_of(gdf)[present]  # one per part from here on, kept in step with `parts`
    parts, src = shapely.get_parts(arr, return_index=True)
    shades = shades[src]
    keep = ~shapely.is_empty(parts)
    parts, shades = parts[keep], shades[keep]

    kinds = {GEOM_KIND[g.geom_type] for g in parts if g.geom_type in GEOM_KIND}
    if not kinds:
        raise HTTPException(422, "No polygons, lines or points found in this file")
    if len(kinds) > 1:
        msg = f"This file mixes {' and '.join(sorted(kinds))}. Split it into one file per geometry type."
        raise HTTPException(422, msg)
    kind = kinds.pop()

    if kind == "polygons":
        parts, src = shapely.get_parts(shapely.make_valid(parts), return_index=True)
        shades = shades[src]
        keep = ~shapely.is_empty(parts)
        parts, shades = parts[keep], shades[keep]
    keep = np.array([GEOM_KIND.get(g.geom_type) == kind for g in parts], dtype=bool)
    parts, shades = parts[keep], shades[keep]

    before = _vertices(parts)
    if kind == "polygons":
        parts = _simplify_polygons(parts)
    elif kind == "lines":
        parts = shapely.simplify(parts, SIMPLIFY_TOL_DEG, preserve_topology=True)
    keep = ~shapely.is_empty(parts)
    parts, shades = parts[keep], shades[keep]

    out = STAGING / f"{token}.{kind}.parquet"
    if kind == "polygons" and shades.any():
        gpd.GeoDataFrame({"shade": shades}, geometry=parts, crs=4326).to_parquet(
            out, compression="zstd"
        )
    else:
        gpd.GeoDataFrame(geometry=parts, crs=4326).to_parquet(out, compression="zstd")
    return kind, {
        "kind": kind,
        "features": len(parts),
        "vertices_before": before,
        "vertices_after": _vertices(parts),
        "bytes_in": len(data),
        "bytes_out": out.stat().st_size,
        "bbox": [float(v) for v in shapely.total_bounds(parts)],
        "geojson": _to_geojson(parts, PREVIEW_MAX_FEATURES, shades),
    }


def _discard(token: str) -> None:
    for p in STAGING.glob(f"{token}.*"):
        p.unlink(missing_ok=True)


def _stage(kind: str, filename: str, data: bytes) -> dict:
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
        elif kind == "svg":
            info = _process_svg(data, STAGING / f"{token}.svg.svg")
        elif kind == "csv":
            info = _process_csv(data, STAGING / f"{token}.csv.csv")
        else:
            _, info = _process_geo(data, suffix, token)
    except HTTPException:
        _discard(token)
        raise
    except Exception as e:
        _discard(token)
        raise HTTPException(422, f"Could not read {filename}: {e}") from e
    return {"token": token, "name": filename, **info}


# ── staging endpoints ──────────────────────────────────────────────────────


@router.post("/clues/stage/{kind}")
async def stage_upload(kind: str, filename: str, request: Request):
    """Raw file bytes in the body (no multipart dependency)."""
    return await run_in_threadpool(_stage, kind, filename, await request.body())


class PathBody(BaseModel):
    path: str


@router.post("/clues/stage-path/{kind}")
async def stage_from_path(kind: str, body: PathBody):
    """Same as above for a file already on this machine (the backend runs locally)."""
    p = Path(body.path.strip().strip('"'))
    if not p.is_file():
        raise HTTPException(404, f"No file at {p}")
    return await run_in_threadpool(_stage, kind, p.name, p.read_bytes())


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


class ClueBody(BaseModel):
    scope: Literal["nat", "reg"]
    info: str
    tags: list[str]
    frequency: int | None = Field(None, ge=1, le=10)
    ease: int | None = Field(None, ge=1, le=10)
    reliability: int | None = Field(None, ge=1, le=10)
    visibility: Literal["visible", "guide-only", "analysis-only"] = "visible"
    blur: bool = False  # blur the clue image until hovered (only kept when the clue has an image)
    max_height: int | None = Field(
        None, ge=100, le=2000
    )  # px cap for the image on the card (only kept with an image)
    source: str = ""  # link to where the information came from (optional)
    relations: list[
        RelationIn
    ] = []  # the full list of relations of this clue (replaces the stored one)
    relation_notes: dict[str, NoteIn] = {}  # sentence for same_as / similar_to
    badge: Literal["subcontinent", "world"] | None = None  # manual uniqueness badge
    files: dict[str, str] = {}  # kind -> staging token
    remove: list[str] = []  # kinds of the clue's own files to delete (e.g. the media changed type)


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


# ── relations ──────────────────────────────────────────────────────────────


def _relations_of(body: ClueBody, cc: str) -> tuple[list[dict], dict]:
    """Normalised relations + notes from the request. Raises 422 on anything inconsistent."""
    rels: list[dict] = []
    seen: set[tuple] = set()
    for r in body.relations:
        item = {"type": r.type, "cc": _cc(r.cc), "id": r.id}
        if r.type == "group" and item["cc"] != cc:
            raise HTTPException(422, "A group can only join clues of the same country")
        key = (item["type"], item["cc"], item["id"])
        if key not in seen:
            seen.add(key)
            rels.append(item)
    present = {r["type"] for r in rels}
    notes: dict[str, dict] = {}
    for t in NOTE_TYPES:
        if t not in present:
            continue
        n = body.relation_notes.get(t)
        text = n.text.strip() if n else ""
        if not text:
            raise HTTPException(422, f"Finish the sentence for the {t.replace('_', ' ')} relation")
        notes[t] = {"verb": n.verb, "text": text}
    return rels, notes


def _set_rels(clue: dict, rels: list[dict]) -> None:
    """Store `rels` on the clue and drop notes of relation types it no longer has."""
    if rels:
        clue["relations"] = rels
    else:
        clue.pop("relations", None)
    present = {r["type"] for r in rels}
    notes = {k: v for k, v in (clue.get("relation_notes") or {}).items() if k in present}
    if notes:
        clue["relation_notes"] = notes
    else:
        clue.pop("relation_notes", None)


def _sync_relations(
    books: dict[str, list[dict]], cc: str, entry: dict, old: list[dict], new: list[dict]
) -> None:
    """Make the clues on the other end mirror `entry`'s relations (old -> new).

    `books` maps country code -> its clue list; the current country's list MUST be the one that is about to
    be saved (so edits to clues of the same country aren't overwritten). Other countries are loaded lazily.
    The caller saves every list in `books` afterwards.
    """

    def items_of(code: str) -> list[dict]:
        if code not in books:
            books[code] = _load(code)
        return books[code]

    def find(code: str, clue_id: str) -> dict | None:
        return next((c for c in items_of(code) if c.get("id") == clue_id), None)

    def keys(rels: list[dict]) -> set[tuple]:
        return {(r["type"], r["cc"], r["id"]) for r in rels}

    old_k, new_k = keys(old), keys(new)
    added, removed = new_k - old_k, old_k - new_k

    for _, code, cid in added:  # validate everything before touching anything
        if code == cc and cid == entry["id"]:
            raise HTTPException(422, "A clue can't be related to itself")
        if find(code, cid) is None:
            raise HTTPException(422, "A related clue no longer exists. Reopen the editor.")

    for t, code, cid in removed:
        target = find(code, cid)
        if target is None:
            continue
        left = [
            r
            for r in target.get("relations") or []
            if not (r["type"] == t and r["cc"] == cc and r["id"] == entry["id"])
        ]
        _set_rels(target, left)

    for t, code, cid in added:
        target = find(code, cid)
        rels = list(target.get("relations") or [])
        back = {"type": t, "cc": cc, "id": entry["id"]}
        if back not in rels:
            rels.append(back)
        target["relations"] = rels
        note = (entry.get("relation_notes") or {}).get(t)
        if note:  # the other side gets the same sentence unless it already has its own
            notes = target.get("relation_notes") or {}
            notes.setdefault(t, dict(note))
            target["relation_notes"] = notes

    if any(t == "group" for t, _, _ in added):
        _close_group(items_of(cc), entry["id"], cc)


def _close_group(items: list[dict], start_id: str, cc: str) -> None:
    """Groups are transitive: if 1 is grouped with 2 and with 3, then 2 and 3 are grouped too.

    Walks the group relations from `start_id` and makes every member of that group point at every other member.
    """
    by_id = {c.get("id"): c for c in items}

    def peers(cid: str) -> list[str]:
        return [
            r["id"]
            for r in by_id[cid].get("relations") or []
            if r["type"] == "group" and r["id"] in by_id
        ]

    members: list[str] = []
    todo = [start_id]
    while todo:
        cid = todo.pop()
        if cid in members or cid not in by_id:
            continue
        members.append(cid)
        todo.extend(peers(cid))

    for cid in members:
        rels = list(by_id[cid].get("relations") or [])
        have = {r["id"] for r in rels if r["type"] == "group"}
        rels.extend(
            {"type": "group", "cc": cc, "id": other}
            for other in members
            if other != cid and other not in have
        )
        by_id[cid]["relations"] = rels


def _countries_with_clues() -> set[str]:
    """Every country that has at least one clue (hidden ones too: they can still be related)."""
    return {
        d.name.upper()
        for d in sorted(CLUES_DIR.glob("??"))
        if d.is_dir() and re.fullmatch(r"[A-Za-z]{2}", d.name) and _load(d.name)
    }


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
def list_clues(cc: str, show_all: Annotated[bool, Query(alias="all")] = False):
    cc = _cc(cc)
    items = _load(cc)
    if not show_all:
        items = [c for c in items if c.get("visibility", "visible") in {"visible", "guide-only"}]
    sub = _SUB_OF.get(cc)
    for c in items:
        if c.get("csv"):
            p = CLUES_DIR / cc / c["csv"]
            if p.is_file():
                c["csv_rows"] = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8"))))
        if c.get("image") or c.get("svg"):
            # rev (the file's mtime) busts the browser cache for ?v=: a file overwritten in place
            # keeps its name, so without this the card could show the old picture after an update.
            rev = 0
            for kind in ("image", "svg"):
                p = CLUES_DIR / cc / c[kind] if c.get(kind) else None
                if p and p.is_file():
                    rev = max(rev, p.stat().st_mtime_ns)
            if rev:
                c["rev"] = rev
        if sub:
            c["subcontinent"] = sub  # for the badge tooltip: "Unique to South America"
    return items


@router.get("/clues/{cc}/files/{name}")
def clue_file(cc: str, name: str):
    # "no-cache" = the browser may keep it but must revalidate, so a file overwritten in place
    # (same name, new content) shows up right away.
    p = _asset(cc, name)
    # An SVG has to be served as such for the card to render it as a picture.
    media = "image/svg+xml" if p.suffix == ".svg" else None
    return FileResponse(p, media_type=media, headers={"Cache-Control": "no-cache"})


@router.get("/clues/{cc}/geo/{name}")
def clue_geo(cc: str, name: str):
    gdf = gpd.read_parquet(_asset(cc, name))
    return _to_geojson(gdf.geometry.to_numpy(), shades=_shades_of(gdf))


@router.post("/clues/{cc}", status_code=201)
def add_clue(cc: str, body: ClueBody):
    cc = _cc(cc)
    location, general = _validate(body)
    source = _clean_source(body)
    rels, notes = _relations_of(body, cc)
    folder = CLUES_DIR / cc
    with _LOCK:
        staged = {k: _staged_path(t, k) for k, t in body.files.items()}
        folder.mkdir(parents=True, exist_ok=True)
        names: dict[str, str] = {}
        try:
            for kind in KINDS:
                if kind in staged:
                    name = _next_name(folder, body.scope, general, staged[kind].suffix)
                    shutil.move(staged[kind], folder / name)
                    names[kind] = name
            entry = {
                "id": uuid.uuid4().hex[:8],
                "scope": body.scope,
                "info": body.info.strip(),
                "tags": [location, general],
                "frequency": body.frequency,
                "ease": body.ease,
                "reliability": body.reliability,
                "visibility": body.visibility,
                "blur": True if body.blur else None,
                "max_height": body.max_height,
                "source": source,
                "relations": rels or None,
                "relation_notes": notes or None,
                "badge": body.badge,
                **names,
            }
            entry = {k: v for k, v in entry.items() if v is not None}
            if "image" not in entry:
                entry.pop("blur", None)
                entry.pop("max_height", None)
            items = [*_load(cc), entry]
            books = {cc: items}
            _sync_relations(books, cc, entry, [], rels)
            for code, its in books.items():
                _save(code, its)
            _set_has(cc, items)
        except Exception:
            for name in names.values():
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


@router.put("/clues/{cc}/{clue_id}")
def update_clue(cc: str, clue_id: str, body: ClueBody):
    """Edit a clue. Text, tags, ratings, visibility, source, relations and badge are replaced; files only when new ones are sent.

    A new file of a kind the clue already has (same extension) overwrites the old file in place, so
    nothing is left behind. A new geometry file replaces whichever geometry the clue had: if it is the
    same geometry kind it is overwritten in place, otherwise the old geometry file is deleted.
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
        staged = {k: _staged_path(t, k) for k, t in body.files.items()}
        replaced = set(staged) | set(body.remove)
        if replaced & set(GEOM_KINDS):
            replaced |= set(GEOM_KINDS)
        names: dict[str, str] = {}
        overwrite: dict[
            str, Path
        ] = {}  # kind -> staged file that goes over the clue's existing file
        try:
            for kind in KINDS:
                if kind not in staged:
                    continue
                old_name = old.get(kind)
                if (
                    old_name
                    and ASSET_RE.fullmatch(old_name)
                    and Path(old_name).suffix == staged[kind].suffix
                    and (folder / old_name).is_file()
                ):
                    # Same slot: keep the file name, swap the content after the yaml is saved.
                    names[kind] = old_name
                    overwrite[kind] = staged[kind]
                    continue
                name = _next_name(folder, body.scope, general, staged[kind].suffix)
                shutil.move(staged[kind], folder / name)
                names[kind] = name
            kept = {k: old[k] for k in KINDS if old.get(k) and k not in replaced}
            entry = {
                "id": old["id"],
                "scope": old["scope"],
                "info": body.info.strip(),
                "tags": [location, general],
                "frequency": body.frequency,
                "ease": body.ease,
                "reliability": body.reliability,
                "visibility": body.visibility,
                "blur": True if body.blur else None,
                "max_height": body.max_height,
                "source": source,
                "relations": rels or None,
                "relation_notes": notes or None,
                "badge": body.badge,
                **kept,
                **names,
            }
            entry = {k: v for k, v in entry.items() if v is not None}
            if "image" not in entry:
                entry.pop("blur", None)
                entry.pop("max_height", None)
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
            raise
        for kind, src in overwrite.items():
            try:
                src.replace(folder / names[kind])
            except OSError as e:
                msg = "Could not overwrite the old file. Close anything using it and try again."
                raise HTTPException(500, msg) from e
        # Old files that no longer belong to the clue (replaced by a differently named file).
        kept_names = set(names.values())
        for kind in replaced:
            old_name = old.get(kind)
            if old_name and old_name not in kept_names and ASSET_RE.fullmatch(old_name):
                (folder / old_name).unlink(missing_ok=True)
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
        for kind in KINDS:
            if gone.get(kind) and ASSET_RE.fullmatch(gone[kind]):
                (CLUES_DIR / cc / gone[kind]).unlink(missing_ok=True)
    return {"ok": True}
