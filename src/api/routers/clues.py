"""clues.py — Per-country clues: stage uploads (convert + simplify), save, edit, list, delete.

Layout:  data/clues/{CC}/clues.yaml  +  data/clues/{CC}/<nat|reg>_<tag>_#####.<ext>
Flow:    POST /clues/stage/{kind}  -> file is converted/simplified into data/clues/.staging, preview returned
         POST /clues/{CC}          -> staged files are renamed into the country folder, entry added to yaml
         PUT  /clues/{CC}/{id}     -> same body; only the files that are sent get replaced
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

import geopandas as gpd
import numpy as np
import shapely
import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse
from PIL import Image, ImageOps
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

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

KINDS = ("image", "csv", "polygons", "lines", "points")
AREA_KINDS = ("csv", "polygons", "lines", "points")
GEOM_KINDS = ("polygons", "lines", "points")
GEOM_KIND = {"Polygon": "polygons", "LineString": "lines", "Point": "points"}
STAGE_KINDS = ("image", "csv", "geometry")

SIMPLIFY_TOL_DEG = 0.0005  # ~55 m. Lower = more faithful, bigger files.
PREVIEW_PRECISION = 1e-5  # ~1 m, only for the GeoJSON sent to the browser
IMAGE_MAX_PX = 1600
IMAGE_QUALITY = 88
PREVIEW_MAX_FEATURES = 5000

ASSET_RE = re.compile(r"(nat|reg)_[a-z]+_\d{5}\.(webp|csv|parquet)")
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
    if not re.fullmatch(r"[0-9a-f]{32}", token) or kind not in KINDS:
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


def _to_geojson(geoms, limit: int | None = None) -> dict:
    if limit and len(geoms) > limit:
        geoms = geoms[:: -(-len(geoms) // limit)]
    g = shapely.set_precision(geoms, PREVIEW_PRECISION)
    g = g[~shapely.is_empty(g)]
    return {
        "type": "GeometryCollection",
        "geometries": [json.loads(s) for s in shapely.to_geojson(g)],
    }


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
    arr = arr[~shapely.is_missing(arr)]
    parts = shapely.get_parts(arr)
    parts = parts[~shapely.is_empty(parts)]

    kinds = {GEOM_KIND[g.geom_type] for g in parts if g.geom_type in GEOM_KIND}
    if not kinds:
        raise HTTPException(422, "No polygons, lines or points found in this file")
    if len(kinds) > 1:
        msg = f"This file mixes {' and '.join(sorted(kinds))}. Split it into one file per geometry type."
        raise HTTPException(422, msg)
    kind = kinds.pop()

    if kind == "polygons":
        parts = shapely.get_parts(shapely.make_valid(parts))
        parts = parts[~shapely.is_empty(parts)]
    parts = parts[np.array([GEOM_KIND.get(g.geom_type) == kind for g in parts], dtype=bool)]

    before = _vertices(parts)
    if kind == "polygons":
        parts = _simplify_polygons(parts)
    elif kind == "lines":
        parts = shapely.simplify(parts, SIMPLIFY_TOL_DEG, preserve_topology=True)
    parts = parts[~shapely.is_empty(parts)]

    out = STAGING / f"{token}.{kind}.parquet"
    gpd.GeoDataFrame(geometry=parts, crs=4326).to_parquet(out, compression="zstd")
    return kind, {
        "kind": kind,
        "features": len(parts),
        "vertices_before": before,
        "vertices_after": _vertices(parts),
        "bytes_in": len(data),
        "bytes_out": out.stat().st_size,
        "bbox": [float(v) for v in shapely.total_bounds(parts)],
        "geojson": _to_geojson(parts, PREVIEW_MAX_FEATURES),
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
    return FileResponse(_staged_path(token, kind))


# ── clues ──────────────────────────────────────────────────────────────────


class ClueBody(BaseModel):
    scope: Literal["nat", "reg"]
    info: str
    tags: list[str]
    frequency: int | None = Field(None, ge=1, le=10)
    ease: int | None = Field(None, ge=1, le=10)
    reliability: int | None = Field(None, ge=1, le=10)
    visibility: Literal["visible", "guide-only", "analysis-only"] = "visible"
    files: dict[str, str] = {}  # kind -> staging token


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
    kinds = set(b.files) | set(have)
    if b.scope == "nat" and "image" not in kinds:
        raise HTTPException(422, "A national clue needs an image")
    if b.scope == "reg" and not any(k in kinds for k in AREA_KINDS):
        raise HTTPException(422, "A regional clue needs a csv, polygons, lines or points file")
    return loc[0], gen[0]


@router.get("/clues/summary")
def clues_summary():
    """Countries that have at least one clue shown in the normal view (must stay above /clues/{cc})."""
    return {"countries": sorted(_summary())}


@router.get("/clues/{cc}")
def list_clues(cc: str, show_all: Annotated[bool, Query(alias="all")] = False):
    cc = _cc(cc)
    items = _load(cc)
    if not show_all:
        items = [c for c in items if c.get("visibility", "visible") in {"visible", "guide-only"}]
    for c in items:
        if c.get("csv"):
            p = CLUES_DIR / cc / c["csv"]
            if p.is_file():
                c["csv_rows"] = list(csv.reader(io.StringIO(p.read_text(encoding="utf-8"))))
    return items


@router.get("/clues/{cc}/files/{name}")
def clue_file(cc: str, name: str):
    return FileResponse(_asset(cc, name))


@router.get("/clues/{cc}/geo/{name}")
def clue_geo(cc: str, name: str):
    return _to_geojson(gpd.read_parquet(_asset(cc, name)).geometry.to_numpy())


@router.post("/clues/{cc}", status_code=201)
def add_clue(cc: str, body: ClueBody):
    cc = _cc(cc)
    location, general = _validate(body)
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
                **names,
            }
            entry = {k: v for k, v in entry.items() if v is not None}
            items = [*_load(cc), entry]
            _save(cc, items)
            _set_has(cc, items)
        except Exception:
            for name in names.values():
                (folder / name).unlink(missing_ok=True)
            raise
    return entry


@router.put("/clues/{cc}/{clue_id}")
def update_clue(cc: str, clue_id: str, body: ClueBody):
    """Edit a clue. Text, tags, ratings and visibility are replaced; files only when new ones are sent
    (a new geometry file replaces whichever geometry the clue had).
    """
    cc = _cc(cc)
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
        replaced = set(staged)
        if replaced & set(GEOM_KINDS):
            replaced |= set(GEOM_KINDS)
        names: dict[str, str] = {}
        try:
            for kind in KINDS:
                if kind in staged:
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
                **kept,
                **names,
            }
            entry = {k: v for k, v in entry.items() if v is not None}
            updated = [entry if c is old else c for c in items]
            _save(cc, updated)
            _set_has(cc, updated)
        except Exception:
            for name in names.values():
                (folder / name).unlink(missing_ok=True)
            raise
        for kind in replaced:
            old_name = old.get(kind)
            if old_name and ASSET_RE.fullmatch(old_name):
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
        _save(cc, remaining)
        _set_has(cc, remaining)
        for kind in KINDS:
            if gone.get(kind) and ASSET_RE.fullmatch(gone[kind]):
                (CLUES_DIR / cc / gone[kind]).unlink(missing_ok=True)
    return {"ok": True}
