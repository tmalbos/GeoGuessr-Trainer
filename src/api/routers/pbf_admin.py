"""pbf_admin.py — Technical tool: read a Geofabrik .osm.pbf, pick a country and export admin boundaries as TopoJSON.

Flow:
    POST /technical/pbf/open     {path}                       -> token + countries in the file
    POST /technical/pbf/upload   (raw body, ?filename=)       -> same, for a file picked in the browser
    POST /technical/pbf/country  {token, country}             -> SLOW, once per country: builds every admin boundary of that
                                                                 country, returns the levels it really has (+ counts, + fills)
    POST /technical/pbf/preview  {token, country, levels, simplify} -> per-level GeoJSON features (fast, cached)
    POST /technical/pbf/export   {token, country, levels, simplify} -> TopoJSON download
    POST /technical/pbf/rename   {token, country, level, gap, name} -> renames one filled-in ("Unnamed N") polygon
    POST /technical/pbf/fill     {token, country, level, source}    -> fills the gaps of a level with the polygons of
                                                                       another level (null = back to "Unnamed N")

How it works (why it is fast after the first load):
  1. Opening the file reads only the relations and remembers each admin relation's member ways. On a big file the
     relations are first pulled out with osmium-tool (C++, multi-threaded, relations only) into a small file that is
     cached on disk (keyed by path, size and modification time), and only that small file is read in Python. So the
     first open of a huge file is a single fast streaming pass and every later open is instant. Without osmium-tool
     the file is read directly (slower, same result).
  2. Choosing a country reads the file once more (nodes + ways, node locations cached by osmium), keeps ONLY the
     ways that admin relations use, and assembles every admin boundary of the file with shapely.
  3. Everything lives in memory, grouped by level. The first time a level is previewed or exported it is finished
     lazily (coastlines are read from the file the first time any level needs them):
       - its boundaries are cut to land (coastlines);
       - the empty parts of the country (land not covered by that level) become one polygon each, named
         "Unnamed N" (flagged `unnamed`, drawn red). The user can rename them, or pick ONE other level whose
         polygons are used to fill that level's gaps instead (clipped to the empty area so nothing is added twice;
         whatever that level leaves empty is still "Unnamed N").
     The finished level is cached; simplified + serialised features are cached per (level, simplification).
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import operator
import tempfile
import threading
from array import array
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool
from starlette.responses import Response

from src.geo import osmium_tool

log = logging.getLogger(
    "uvicorn.error"
)  # same logger uvicorn prints, so these lines show in the console
router = APIRouter()

MAX_DATASETS = 3
MAX_COUNTRIES = 3  # extracted countries kept in memory per file
MAX_PREVIEW_CACHE = 60  # (level, pct) entries per country
PREVIEW_PCT = 5
BBOX_PAD = 0.5  # degrees around the boundaries when picking coastlines for the cut
MIN_LEFTOVER = 1e-12  # deg2: smaller uncovered bits of a boundary are numerical noise
SMALL_SHARE = (
    0.001  # a gap smaller than this share of the country's area is a fragment of a neighbour
)
TOUCH_TOL = 1e-5  # deg (~1 m): "touching" tolerance for gaps against the country edge / a boundary
MIN_PIECE = 1e-7  # deg2: smaller filled pieces are dropped
CACHE_DIR = Path(tempfile.gettempdir()) / "geoguessr-pbf-admin"
UNNAMED_COLOR = (
    "#e5484d"  # filled-in gaps that still have their default name (keep equal to the frontend)
)
_LEVEL_COLORS = (
    "#4e79a7",
    "#f28e2b",
    "#e15759",
    "#76b7b2",
    "#59a14f",
    "#edc948",
    "#b07aa1",
    "#ff9da7",
    "#9c755f",
    "#bab0ac",
)

_DATASETS: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()
_COMPUTE = threading.Lock()  # one extraction at a time (CPU / memory heavy)
_LAND = threading.RLock()  # one level finish (coast cut + gap fill) at a time


# ── helpers ────────────────────────────────────────────────────────────────


def _clean(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and math.isnan(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _level(value) -> int | None:
    text = _clean(value).strip()
    return int(text) if text.isdigit() else None


def _polys(geom):
    if geom is None or geom.is_empty:
        return None
    parts = [p for p in shapely.get_parts(geom) if p.geom_type == "Polygon" and not p.is_empty]
    if not parts:
        return None
    return (
        parts[0] if len(parts) == 1 else shapely.multipolygons(parts)
    )  # parts of a valid geometry never overlap


def _safe(op, a, b):
    try:
        return op(a, b)
    except Exception:  # noqa: BLE001
        return op(shapely.make_valid(a), shapely.make_valid(b))


def _country_code(tags) -> str:
    for key in ("ISO3166-1:alpha2", "ISO3166-1"):
        value = (tags.get(key) or "").strip().upper()
        if len(value) == 2 and value.isalpha():
            return value
    return ""


def _dataset(token: str) -> dict:
    with _LOCK:
        ds = _DATASETS.get(token)
        if ds is None:
            raise HTTPException(410, "That PBF expired. Open the file again.")
        _DATASETS.move_to_end(token)
        return ds


def _oneline(e: Exception) -> str:
    # api.js only shows backend text that is short, one line and not "technical"; keep it that way.
    text = f"{type(e).__name__}: {e}".replace("\n", " ").replace("Exception", "Error")
    return text[:140]


# ── step 1: open the file (relations only) ─────────────────────────────────


def _scan(path: Path) -> tuple[list[dict], dict[int, dict]]:
    try:
        import osmium
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The osmium package is not installed on the server.") from e

    countries: list[dict] = []
    seen_ids: set[str] = set()
    seen_codes: set[str] = set()
    rels: dict[int, dict] = {}

    for obj in osmium.FileProcessor(str(path), osmium.osm.RELATION):
        tags = obj.tags
        if tags.get("boundary") != "administrative":
            continue
        if tags.get("type") == "multilinestring":  # border lines between countries, not areas
            continue
        level = _level(tags.get("admin_level"))
        if level is None:
            continue
        name = tags.get("name:en") or tags.get("name") or ""

        if level == 2 and name and str(obj.id) not in seen_ids:
            seen_ids.add(str(obj.id))
            code = _country_code(tags)
            if not (code and code in seen_codes):
                if code:
                    seen_codes.add(code)
                countries.append({"id": str(obj.id), "name": str(name), "code": code})

        outer, inner = array("q"), array("q")
        for m in obj.members:
            if str(m.type) not in {"w", "way"}:
                continue
            (inner if m.role == "inner" else outer).append(m.ref)
        if outer:
            rels[obj.id] = {
                "level": level,
                "name": str(name or "?"),
                "iso": str(
                    tags.get("ISO3166-2")
                    or tags.get("ISO3166-1:alpha2")
                    or tags.get("ISO3166-1")
                    or ""
                ),
                "outer": outer,
                "inner": inner,
            }

    countries.sort(key=lambda c: c["name"].casefold())
    return countries, rels


def _relations_file(path: Path) -> Path:
    """The file to scan for admin relations: a small cached copy holding only boundary=administrative relations
    (made by osmium-tool in one fast pass, no nodes / ways), or the original when osmium-tool isn't available.
    """
    try:
        osmium_tool.require()
    except osmium_tool.OsmiumMissingError:
        return path
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    out = CACHE_DIR / f"admin-rels-{key}.osm.pbf"
    if out.is_file() and out.stat().st_size > 0:
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    part = CACHE_DIR / f"admin-rels-{key}.part.osm.pbf"
    try:
        osmium_tool.run(
            [
                "tags-filter",
                "--omit-referenced",
                str(path),
                "r/boundary=administrative",
                "--output",
                str(part),
                "--overwrite",
            ]
        )
    except osmium_tool.OsmiumError:
        log.warning("osmium tags-filter failed; scanning the file directly", exc_info=True)
        part.unlink(missing_ok=True)
        return path
    part.replace(out)
    return out


def _open(path_text: str, temporary: bool = False, display_name: str | None = None) -> dict:
    path = Path(path_text.strip().strip('"'))
    if not path.is_file():
        raise HTTPException(404, f"No file at {path}")
    if not path.name.lower().endswith(".pbf"):
        raise HTTPException(422, "That is not an .osm.pbf file")
    try:
        countries, rels = _scan(_relations_file(path))
    except HTTPException:
        raise
    except Exception as e:
        log.exception("PBF scan failed")
        raise HTTPException(500, f"Could not read this file: {_oneline(e)}") from e
    if not countries:
        raise HTTPException(
            422,
            "No country boundaries (boundary=administrative, admin_level=2) were found in this file.",
        )
    token = uuid4().hex
    ds = {
        "token": token,
        "path": path,
        "name": display_name or path.name,
        "size": path.stat().st_size,
        "countries": countries,
        "rels": rels,
        "temporary": temporary,
        "extracted": OrderedDict(),  # country id -> data
    }
    with _LOCK:
        _DATASETS[token] = ds
        while len(_DATASETS) > MAX_DATASETS:
            _, old = _DATASETS.popitem(last=False)
            if old.get("temporary"):
                old["path"].unlink(missing_ok=True)
    return {
        "token": token,
        "file": {"name": ds["name"], "size": ds["size"]},
        "countries": countries,
    }


# ── step 2: build one country ──────────────────────────────────────────────


def _read_ways(path: Path, wanted: set[int]) -> dict[int, np.ndarray]:
    """One pass over the file. Returns {way id: (n, 2) lon/lat array} for the wanted ways only.
    osmium filters out untagged nodes and unwanted ways before Python sees them (the node locations are still
    cached for the ways), so Python only touches the boundary ways.
    Ways with a missing node (extract edge) are skipped, and so are the relations that need them.
    """
    import osmium

    fp = osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY).with_locations()
    try:
        fp = fp.with_filter(osmium.filter.EmptyTagFilter().enable_for(osmium.osm.NODE))
        fp = fp.with_filter(osmium.filter.IdFilter(list(wanted)).enable_for(osmium.osm.WAY))
    except Exception:  # older pyosmium: slower, same result
        log.warning("osmium filters unavailable, reading without them", exc_info=True)

    out: dict[int, np.ndarray] = {}
    for o in fp:
        if not o.is_way() or o.id not in wanted:
            continue
        try:
            xy = np.array([(n.lon, n.lat) for n in o.nodes], dtype=float)
        except Exception:  # noqa: BLE001  # a node location is unknown
            continue
        if len(xy) < 2:
            continue
        out[o.id] = xy
    return out


def _read_coast(path: Path, bbox) -> tuple[np.ndarray, np.ndarray]:
    """One pass over the file. Returns the natural=coastline ways as directed LineStrings (land is on their left)
    and the OSM id of each way.
    """
    import osmium

    fp = osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY).with_locations()
    try:
        fp = fp.with_filter(osmium.filter.EmptyTagFilter().enable_for(osmium.osm.NODE))
        fp = fp.with_filter(
            osmium.filter.TagFilter(("natural", "coastline")).enable_for(osmium.osm.WAY)
        )
    except Exception:  # older pyosmium: slower, same result
        log.warning("osmium filters unavailable, reading without them", exc_info=True)

    lines: list = []
    way_ids: list = []
    for o in fp:
        if not o.is_way() or o.tags.get("natural") != "coastline":
            continue
        try:
            xy = np.array([(n.lon, n.lat) for n in o.nodes], dtype=float)
        except Exception:  # noqa: BLE001  # a node location is unknown
            continue
        if len(xy) < 2:
            continue
        if bbox is not None and (
            xy[:, 0].max() < bbox[0]
            or xy[:, 0].min() > bbox[2]
            or xy[:, 1].max() < bbox[1]
            or xy[:, 1].min() > bbox[3]
        ):
            continue
        lines.append(shapely.linestrings(xy))
        way_ids.append(o.id)
    arr = np.empty(len(lines), dtype=object)
    arr[:] = lines
    return arr, np.array(way_ids, dtype=np.int64)


def _faces(way_ids, ways: dict[int, np.ndarray]):
    """Filled polygon(s) of a set of ways. Fast path: the ways chain end to end into closed rings (the normal case)
    and no noding is needed. Otherwise the slower polygonize path.
    """
    lines = [shapely.linestrings(ways[i]) for i in way_ids if i in ways]
    if not lines:
        return None
    merged = shapely.line_merge(shapely.multilinestrings(lines))
    parts = shapely.get_parts(merged)
    if (
        len(parts)
        and bool(np.all(shapely.is_closed(parts)))
        and all(len(p.coords) >= 4 for p in parts)
    ):
        polys = [shapely.Polygon(p.coords) for p in parts]
        return (
            polys[0]
            if len(polys) == 1
            else shapely.union_all([g if g.is_valid else shapely.make_valid(g) for g in polys])
        )
    faces = [
        p
        for p in shapely.get_parts(
            shapely.polygonize(shapely.get_parts(shapely.line_merge(shapely.union_all(lines))))
        )
        if p.geom_type == "Polygon" and not p.is_empty
    ]
    return shapely.union_all(faces) if faces else None


def _assemble(rel: dict, ways: dict[int, np.ndarray]):
    """Outer rings (filled) minus inner rings. None when the relation is incomplete or its ways don't close."""
    if any(i not in ways for i in rel["outer"]):
        return None
    g = _faces(rel["outer"], ways)
    if g is None:
        return None
    if rel["inner"]:
        h = _faces(rel["inner"], ways)
        if h is not None:
            try:
                g = shapely.difference(g, h)
            except Exception:  # noqa: BLE001
                g = shapely.difference(shapely.make_valid(g), shapely.make_valid(h))
    if not shapely.is_valid(g):
        g = shapely.make_valid(g)
    return _polys(g)


def _extract(ds: dict, country: str) -> dict:
    country_name = next((c["name"] for c in ds["countries"] if c["id"] == country), None)
    if country_name is None:
        raise HTTPException(404, "That country is no longer in the open file.")

    rels: dict[int, dict] = ds["rels"]
    wanted: set[int] = set()
    for r in rels.values():
        wanted.update(r["outer"])
        wanted.update(r["inner"])

    ways = _read_ways(ds["path"], wanted)
    if not ways:
        raise HTTPException(422, "No boundary geometry could be read from this file.")

    ids: list[int] = []
    geoms: list = []
    for rid, r in rels.items():
        g = _assemble(r, ways)
        if g is not None:
            ids.append(rid)
            geoms.append(g)
    del ways
    if not geoms:
        raise HTTPException(422, "No complete administrative boundaries were found in this file.")

    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    areas = shapely.area(arr)
    lv = np.array([rels[i]["level"] for i in ids])

    by_level: dict[int, dict] = {}
    for level in sorted(set(lv.tolist())):
        sel = np.flatnonzero(lv == level)
        sel = sel[np.argsort(-areas[sel])]  # big areas first, so small ones are drawn on top
        props = []
        for k in sel:
            rid = ids[k]
            r = rels[rid]
            props.append(
                {"name": r["name"], "admin_level": level, "osm_id": str(rid), "iso": r["iso"]}
            )
        by_level[level] = {"props": props, "geoms": arr[sel]}

    cminx, cminy, cmaxx, cmaxy = (float(v) for v in shapely.total_bounds(arr))
    cbox = (cminx - BBOX_PAD, cminy - BBOX_PAD, cmaxx + BBOX_PAD, cmaxy + BBOX_PAD)

    cid = int(country) if country.isdigit() else None
    country_geom = geoms[ids.index(cid)] if cid in ids else None

    return {
        "country": country_name,
        "by_level": by_level,
        "levels": [{"level": level, "count": len(v["props"])} for level, v in by_level.items()],
        "diag": max(math.hypot(cmaxx - cminx, cmaxy - cminy), 1e-9),
        "path": ds["path"],
        "coast_loaded": False,  # coastlines are read the first time a level is finished
        "coast": None,
        "coast_ids": None,
        "tree": None,
        "cbox": cbox,
        "country_geom": country_geom,  # the selected country's own boundary (None when incomplete in the file)
        "country_land": None,  # ...cut to land, computed together with the coastlines
        "country_land_ready": False,
        "trees": {},  # level -> STRtree of the raw boundaries (for the gap filling)
        "land": {},  # level -> {"props", "geoms"}: finished (cut to land + gaps filled)
        "fill": {},  # level -> other level whose polygons fill its gaps (absent = "Unnamed N")
        "renames": {},  # level -> {gap number: custom name}
        "cache": OrderedDict(),
    }


def _load(ds: dict, country: str) -> dict:
    with _LOCK:
        hit = ds["extracted"].get(country)
        if hit is not None:
            ds["extracted"].move_to_end(country)
            return hit
    with _COMPUTE:
        with _LOCK:
            hit = ds["extracted"].get(country)
        if hit is not None:
            return hit
        try:
            data = _extract(ds, country)
        except HTTPException:
            raise
        except Exception as e:
            log.exception("PBF extraction failed")
            raise HTTPException(500, f"Could not read this country: {_oneline(e)}") from e
        with _LOCK:
            ds["extracted"][country] = data
            while len(ds["extracted"]) > MAX_COUNTRIES:
                ds["extracted"].popitem(last=False)
        return data


# ── land only: cut boundaries with the coastlines ──────────────────────────

# Per ray direction (east, west, north, south): the map to "along the ray, across the ray" coordinates, and where
# along the ray a bounding box (west, south, east, north) begins.
_RAY_TF = (
    operator.itemgetter((slice(None), 0), (slice(None), 1)),
    lambda a: (-a[:, 0], a[:, 1]),
    operator.itemgetter((slice(None), 1), (slice(None), 0)),
    lambda a: (-a[:, 1], a[:, 0]),
)
_RAY_START = (
    operator.itemgetter((slice(None), 0)),
    lambda b: -b[:, 2],
    operator.itemgetter((slice(None), 1)),
    lambda b: -b[:, 3],
)


def _nearest_crossing(k: int, px: float, py: float, pxk: float, pyk: float, way):
    """Nearest crossing of one coastline way with the ray from (px, py) in direction k.
    Returns (distance along the ray, point is on land) or None when the way is not crossed (or only touched).
    """
    xy = shapely.get_coordinates(way)
    a, b = xy[:-1], xy[1:]
    ax_, ay_ = _RAY_TF[k](a)
    bx_, by_ = _RAY_TF[k](b)
    crossing = ((ay_ <= pyk) & (by_ > pyk)) | ((by_ <= pyk) & (ay_ > pyk))
    if not crossing.any():
        return None
    t = (pyk - ay_[crossing]) / (by_[crossing] - ay_[crossing])
    xc = ax_[crossing] + t * (bx_[crossing] - ax_[crossing])
    ahead = xc > pxk
    if not ahead.any():
        return None
    i = np.flatnonzero(crossing)[ahead][np.argmin(xc[ahead])]
    cross = (b[i, 0] - a[i, 0]) * (py - a[i, 1]) - (b[i, 1] - a[i, 1]) * (px - a[i, 0])
    if cross == 0:
        return None
    return float(xc[ahead].min()), bool(cross > 0)


def _is_land(px: float, py: float, coast: np.ndarray, tree, cbox) -> tuple[bool | None, int | None]:
    """Is the point on land? Exact, no tuning: walk from the point to the nearest coastline and look at which
    side of that (directed) coastline the point is on. OSM: land is on the LEFT of the way direction.
    Between two coastline crossings the answer cannot change, so the nearest crossing decides.
    East is tried first, then west, north, south. Returns (land, index of the deciding coastline); land is None
    when no coastline is reachable in any direction.
    The crossed ways are visited nearest-first and the search stops as soon as the next way starts beyond the
    nearest crossing found, so a point among thousands of islands only reads the few ways close to it.
    """
    w, s, e, n = cbox
    p = np.array([[px, py]])
    for k, end in enumerate(((e, py), (w, py), (px, n), (px, s))):
        cand = tree.query(shapely.linestrings([(px, py), end]), predicate="intersects")
        if not len(cand):
            continue
        pxk, pyk = (v[0] for v in _RAY_TF[k](p))
        start = _RAY_START[k](shapely.bounds(coast[cand]))
        best = None  # (distance along the ray, land, way index)
        for j in np.argsort(start):
            if best is not None and start[j] > best[0]:
                break  # every remaining way begins beyond the nearest crossing found
            hit = _nearest_crossing(k, px, py, pxk, pyk, coast[cand[j]])
            if hit is not None and (best is None or hit[0] < best[0]):
                best = (hit[0], hit[1], int(cand[j]))
        if best is not None:
            return best[1], best[2]
    return None, None


def _land_only(g, coast: np.ndarray, tree, cbox) -> tuple:
    """(land part of boundary `g` or None when none of it is land, removed sea parts, coastline gaps).
    Each removed part is (area deg2, lat, lon, index of the coastline that decided it).
    Each gap is (length km, lat, lon, index of the coastline): a coastline that ends in the middle of `g`
    instead of closing, which lets land and sea run into each other.

    The boundary is split by the coastlines that touch it into faces (so nothing is cut when no coastline
    touches it). The faces that lie inside `g` tile it exactly, because they come from the noded outline of `g`
    plus the coastlines. Every face is then tested with _is_land and only the faces that are certainly sea are
    removed. The land faces share their edges exactly, so they are merged with a coverage union, which only
    dissolves those shared edges instead of running a full overlay.
    """
    shapely.prepare(g)
    hits = tree.query(g, predicate="intersects")
    parts: list = []
    gaps: list = []
    if len(hits):
        # The coastlines are NOT cut to `g`: they are noded together with its boundary, so every crossing becomes
        # a node of both lines and the faces close exactly. (Cutting them first leaves ends that miss the
        # boundary by rounding, and those become loose ends.) They are only trimmed to a box around `g` for speed.
        w, s_, e, n = g.bounds
        margin = 0.1
        lines = shapely.clip_by_rect(
            shapely.multilinestrings(coast[hits]), w - margin, s_ - margin, e + margin, n + margin
        )
        noded = shapely.union_all([shapely.boundary(g), lines])
        poly_c, _cuts, dangles, _bad = shapely.polygonize_full(shapely.get_parts(noded))
        faces = [
            f for f in shapely.get_parts(poly_c) if f.geom_type == "Polygon" and not f.is_empty
        ]
        for d in shapely.get_parts(dangles):
            if d.is_empty or d.length < 5e-4:
                continue
            mid = shapely.line_interpolate_point(d, 0.5, normalized=True)
            if not shapely.covers(g, mid):
                continue
            km = d.length * 111.32 * math.cos(math.radians(mid.y))
            gaps.append((km, mid.y, mid.x, int(tree.nearest(mid))))
        gaps.sort(reverse=True)
        gaps = gaps[:5]
        if faces:
            farr = np.empty(len(faces), dtype=object)
            farr[:] = faces
            farr = farr[np.asarray(shapely.covers(g, shapely.point_on_surface(farr)), dtype=bool)]
            parts = list(farr)
    if not parts:
        parts = [q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and not q.is_empty]

    land, sea = [], []
    for q in parts:
        pt = shapely.point_on_surface(q)
        is_land, way = _is_land(pt.x, pt.y, coast, tree, cbox)
        if is_land is False:
            sea.append((q.area, pt.y, pt.x, way))
        else:
            land.append(q)
    if not sea:
        return g, sea, gaps
    return (_polys(shapely.coverage_union_all(land)) if land else None), sea, gaps


def _cut_level(data: dict, level: int) -> dict:
    """One level's boundaries cut to land. Boundaries that are entirely sea are dropped. Caller holds _LAND."""
    lv = data["by_level"][level]
    if data["tree"] is None:
        return lv
    props, geoms = [], []
    for p, g in zip(lv["props"], lv["geoms"], strict=True):
        try:
            ng, sea, gaps = _land_only(g, data["coast"], data["tree"], data["cbox"])
        except Exception:
            log.warning("Coastline cut failed for %s", p["name"], exc_info=True)
            ng, sea, gaps = g, [], []
        if sea:
            kept = 0.0 if ng is None else 100 * ng.area / (g.area or 1e-12)
            log.info(
                "Coast cut ADM%d %s: %d sea part(s) removed, %.1f%% of its area kept",
                level,
                p["name"],
                len(sea),
                kept,
            )
            for area, lat, lon, way in sea:
                km2 = area * 111.32**2 * math.cos(math.radians(lat))
                log.info(
                    "    removed %.3f km2 at %.5f, %.5f (decided by coastline way %s)",
                    km2,
                    lat,
                    lon,
                    data["coast_ids"][way],
                )
        for km, lat, lon, way in gaps:
            log.info(
                "    coastline ends unclosed (%.2f km of loose line) near %.5f, %.5f (way %s)",
                km,
                lat,
                lon,
                data["coast_ids"][way],
            )
        if ng is not None and not ng.is_empty:
            props.append(p)
            geoms.append(ng)
        else:
            log.info("Coast cut ADM%d %s: DROPPED (nothing left on land)", level, p["name"])
    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    return {"props": props, "geoms": arr}


def _ensure_coast(data: dict) -> None:
    """Read the coastlines (second pass over the file) the first time a level is finished, and cut the
    country's own boundary to land (the area the gaps are measured in).
    """
    with _LAND:
        if data["coast_loaded"]:
            return
        try:
            coast, coast_ids = _read_coast(data["path"], data["cbox"])
            data["coast"], data["coast_ids"] = coast, coast_ids
            data["tree"] = shapely.STRtree(coast) if len(coast) else None
        except Exception:
            log.warning("Could not read the coastlines; boundaries are left uncut", exc_info=True)
        data["coast_loaded"] = True


def _country_land(data: dict):
    """The selected country's boundary cut to land (cached), or None when the file doesn't hold it whole."""
    with _LAND:
        if data["country_land_ready"]:
            return data["country_land"]
        g = data["country_geom"]
        if g is not None and data["tree"] is not None:
            try:
                ng, _sea, _gaps = _land_only(g, data["coast"], data["tree"], data["cbox"])
                g = ng
            except Exception:
                log.warning("Coastline cut failed for the country boundary", exc_info=True)
        data["country_land"] = g
        data["country_land_ready"] = True
        return g


# ── gap filling: the empty land of a level ─────────────────────────────────


def _level_tree(data: dict, level: int):
    t = data["trees"].get(level)
    if t is None:
        t = data["trees"][level] = shapely.STRtree(data["by_level"][level]["geoms"])
    return t


def _neighbour(piece, geoms: list, tree) -> int | None:
    """Index of the closest boundary to the gap `piece`, touching it or not (an island has nothing to touch).
    Ties (several boundaries touching it) go to the one closest to the piece's centre.
    """
    cand = tree.query_nearest(piece, all_matches=True)
    if not len(cand):
        return None
    centre = shapely.centroid(piece)
    return int(min(cand, key=lambda i: float(shapely.distance(centre, geoms[i]))))


def _fill_gaps(data: dict, level: int, base: dict) -> dict:
    """Handle the land of the country that `base` (a level cut to land) leaves empty. Each separate empty
    polygon is either:
      - a fragment: smaller than SMALL_SHARE of the country and touching the country's edge (coast / border;
        an island always does);
      - otherwise its own polygon, "Unnamed N" (flagged `unnamed`, `gap` = N).
    When the user chose another level to fill this one (data["fill"][level]), that level's polygons are first
    clipped to the empty area that is not a fragment (nothing is added twice) and only what it still leaves empty
    becomes "Unnamed N". Custom names (data["renames"]) replace the default ones.
    The fragments are assigned last, each one unioned into the closest polygon of everything that exists by then
    (the level's own boundaries, the ones filled from another level and the "Unnamed N" ones), touching or not.
    No gap is simplified or rounded.
    """
    country = _country_land(data)
    if country is None or country.is_empty:
        return base
    geoms = list(base["geoms"])
    cover = shapely.union_all(geoms) if geoms else None
    gap = country if cover is None else _safe(shapely.difference, country, cover)
    if gap is None or gap.is_empty:
        return base
    pieces = [q for q in shapely.get_parts(gap) if q.geom_type == "Polygon" and not q.is_empty]
    if not pieces:
        return base

    small = SMALL_SHARE * country.area
    edge = shapely.boundary(country)
    fragments: list = []
    rest: list = []
    for q in pieces:
        if q.area < small and shapely.dwithin(q, edge, TOUCH_TOL):
            fragments.append(q)
        else:
            rest.append(q)

    props, out = list(base["props"]), list(geoms)
    gap = _polys(shapely.union_all(rest)) if rest else None
    src = data["fill"].get(level)
    sources = [src] if src is not None and src != level and src in data["by_level"] else []
    from_other = 0
    for m in sources:
        if gap is None:
            break
        lv = data["by_level"][m]
        shapely.prepare(gap)
        added: list = []
        for i in _level_tree(data, m).query(gap, predicate="intersects"):
            poly = lv["geoms"][i]
            local = shapely.clip_by_rect(gap, *poly.bounds)
            piece = _polys(_safe(shapely.intersection, poly, local))
            if piece is None or piece.area < MIN_PIECE:
                continue
            if added:  # same-level boundaries that overlap each other: the first one keeps the area
                arr = np.empty(len(added), dtype=object)
                arr[:] = [g for _, g in added]
                near = arr[shapely.intersects(piece, arr)]
                if len(near):
                    piece = _polys(_safe(shapely.difference, piece, shapely.union_all(near)))
                    if piece is None or piece.area < MIN_PIECE:
                        continue
            p = lv["props"][i]
            added.append(({**p, "admin_level": level, "from_level": m}, piece))
        if added:
            from_other += len(added)
            props.extend(p for p, _ in added)
            out.extend(g for _, g in added)
            gap = _polys(_safe(shapely.difference, gap, shapely.union_all([g for _, g in added])))

    names = data["renames"].get(level, {})
    unnamed = 0
    if gap is not None and data["tree"] is not None:  # without coastlines the leftover may be sea
        for q in shapely.get_parts(gap):
            if q.geom_type != "Polygon" or q.area < MIN_PIECE:
                continue
            unnamed += 1
            custom = names.get(unnamed)
            props.append(
                {
                    "name": custom or f"Unnamed {unnamed}",
                    "admin_level": level,
                    "osm_id": "",
                    "iso": "",
                    "gap": unnamed,
                    "unnamed": not custom,
                }
            )
            out.append(q)

    merged: dict[int, list] = {}
    if fragments and out:
        tree = shapely.STRtree(np.array(out, dtype=object))
        for q in fragments:
            i = _neighbour(q, out, tree)
            if i is not None:
                merged.setdefault(i, []).append(q)
        for i, qs in merged.items():
            g = _polys(_safe(shapely.union, out[i], shapely.union_all(qs)))
            if g is not None:
                out[i] = g

    log.info(
        "Gap fill ADM%d: %d fragment(s) merged, %d from another level, %d unnamed",
        level,
        sum(map(len, merged.values())),
        from_other,
        unnamed,
    )
    arr = np.empty(len(out), dtype=object)
    arr[:] = out
    return {"props": props, "geoms": arr}


def _final_level(data: dict, level: int) -> dict:
    """One level as it is exported: cut to land, then gaps filled. Computed on first use, then cached."""
    with _LAND:
        hit = data["land"].get(level)
        if hit is not None:
            return hit
        _ensure_coast(data)
        hit = _fill_gaps(data, level, _cut_level(data, level))
        data["land"][level] = hit
        return hit


def _current(data: dict, level: int) -> dict:
    return _final_level(data, level)


def _drop_preview(data: dict, level: int) -> None:
    for key in [k for k in data["cache"] if k[0] == level]:
        del data["cache"][key]


def _rename(data: dict, level: int, gap: int, name: str) -> None:
    """Give a filled-in polygon a custom name (empty = back to "Unnamed N")."""
    name = name.strip()
    with _LAND:
        names = data["renames"].setdefault(level, {})
        if name:
            names[gap] = name
        else:
            names.pop(gap, None)
        hit = data["land"].get(level)
        if hit is not None:
            for p in hit["props"]:
                if p.get("gap") == gap:
                    p["name"] = name or f"Unnamed {gap}"
                    p["unnamed"] = not name
        _drop_preview(data, level)


def _set_fill(data: dict, level: int, source: int | None) -> None:
    """Choose which level fills the gaps of `level` (None = leave them as "Unnamed N"). Custom names are reset,
    because the gaps change.
    """
    with _LAND:
        if source is None:
            data["fill"].pop(level, None)
        else:
            data["fill"][level] = source
        data["renames"].pop(level, None)
        data["land"].pop(level, None)
        _drop_preview(data, level)


def _merge(data: dict, level: int, idxs: list[int]) -> None:
    """Merge any number of polygons of a finished level into one (the first one that keeps its name wins)."""
    with _LAND:
        item = _current(data, level)
        ids = sorted(set(idxs))
        if len(ids) < 2 or ids[0] < 0 or ids[-1] >= len(item["props"]):
            raise HTTPException(422, "Pick at least two different polygons.")
        merged = item["geoms"][ids[0]]
        for i in ids[1:]:
            merged = _safe(shapely.union, merged, item["geoms"][i])
        merged = _polys(merged)
        if merged is None:
            raise HTTPException(422, "Those polygons could not be merged.")
        keep = next(
            (item["props"][i] for i in ids if not item["props"][i].get("unnamed")),
            item["props"][ids[0]],
        )
        drop = set(ids[1:])
        props = [p for i, p in enumerate(item["props"]) if i not in drop]
        geoms = item["geoms"].copy()
        props[ids[0] - sum(1 for d in drop if d < ids[0])] = keep
        geoms[ids[0]] = merged
        data["land"][level] = {"props": props, "geoms": np.delete(geoms, list(drop))}
        _drop_preview(data, level)


# ── step 3: preview / export (from memory) ─────────────────────────────────


def _color(level: int) -> str:
    return _LEVEL_COLORS[level % len(_LEVEL_COLORS)]


def _simplify(arr: np.ndarray, tol: float) -> np.ndarray:
    """Simplify ALL polygons of one level together as a coverage: a shared border is simplified once and stays
    identical on both sides, so no gaps or overlaps appear (simplifying each polygon alone does that).
    """
    if tol <= 0 or len(arr) == 0:
        return arr
    if not hasattr(shapely, "coverage_simplify"):
        raise HTTPException(
            503, "Simplification needs shapely 2.1 or newer. Run: pip install -U shapely"
        )
    try:
        return shapely.coverage_simplify(arr, tol)
    except Exception:  # not a valid coverage (overlapping duplicates): better heavy than wrong
        log.warning("coverage_simplify failed, level left unsimplified", exc_info=True)
        return arr


def _tol(data: dict, pct: int) -> float:
    return 0.0 if pct <= 0 else data["diag"] * (pct / 100) * 0.01


def _preview_features(data: dict, level: int, pct: int) -> list[str]:
    """Serialised GeoJSON features of one level at one simplification; computed once, then cached."""
    key = (level, pct)
    hit = data["cache"].get(key)
    if hit is None:
        lv = _current(data, level)
        geoms = _simplify(lv["geoms"], _tol(data, pct))
        gj = shapely.to_geojson(geoms)
        hit = [
            '{"type":"Feature","properties":'
            + json.dumps(
                {
                    **p,
                    "idx": i,
                    "tag": f"ADM{level} · {p['name']}",
                    "color": UNNAMED_COLOR if p.get("unnamed") else _color(level),
                    "shade": 0,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + ',"geometry":'
            + g
            + "}"
            for i, (p, g) in enumerate(zip(lv["props"], gj, strict=True))
        ]
        data["cache"][key] = hit
        while len(data["cache"]) > MAX_PREVIEW_CACHE:
            data["cache"].popitem(last=False)
    return hit


def _preview_body(data: dict, levels: list[int], pct: int) -> str:
    parts = [
        f'"{lv}":[{",".join(_preview_features(data, lv, pct))}]'
        for lv in levels
        if lv in data["by_level"]
    ]
    return '{"pct":%d,"levels":{%s}}' % (pct, ",".join(parts))


def _export(data: dict, country_label: str, levels: list[int], simplify: int) -> tuple[str, str]:
    try:
        from topojson import Topology
    except ImportError as e:  # pragma: no cover
        raise HTTPException(503, "The topojson package is not installed on the server.") from e

    chosen = [lv for lv in levels if lv in data["by_level"]]
    if not chosen:
        raise HTTPException(422, "Pick at least one admin level")
    feats = []
    feat_levels = []
    for lv in chosen:
        item = _current(data, lv)
        geoms = _simplify(item["geoms"], _tol(data, simplify))
        for p, g in zip(item["props"], geoms, strict=True):
            feats.append(
                {
                    "type": "Feature",
                    "properties": {
                        "COUNTRY": data["country"],
                        "ISO": p["iso"],
                        "SUBDIVISION_NAME": p["name"],
                    },
                    "geometry": json.loads(shapely.to_geojson(g)),
                }
            )
            feat_levels.append(lv)
    if not feats:
        raise HTTPException(422, "Nothing of these levels is on land.")
    if len(feats) == 1 and feat_levels[0] < 8:
        del feats[0]["properties"]["SUBDIVISION_NAME"]
    topology = Topology({"type": "FeatureCollection", "features": feats}, object_name="ADM")
    return topology.to_json(), f"{country_label}-ADM{'-'.join(map(str, chosen))}.topojson"


# ── API ────────────────────────────────────────────────────────────────────


class OpenBody(BaseModel):
    path: str = Field(min_length=1)


class CountryBody(BaseModel):
    token: str
    country: str


class ExtractBody(CountryBody):
    levels: list[int] = Field(default_factory=list)
    simplify: int = Field(0, ge=0, le=100)


class RenameBody(CountryBody):
    level: int
    gap: int = Field(ge=1)
    name: str = Field("", max_length=120)


class FillBody(CountryBody):
    level: int
    source: int | None = None


class MergeBody(CountryBody):
    level: int
    idxs: list[int] = Field(min_length=2, max_length=1000)


def _levels(values: list[int]) -> list[int]:
    return sorted({int(v) for v in values if 1 <= int(v) <= 20})


@router.post("/technical/pbf/open")
async def open_pbf(body: OpenBody):
    return await run_in_threadpool(_open, body.path)


@router.post("/technical/pbf/upload")
async def upload_pbf(request: Request, filename: str | None = None):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"upload-{uuid4().hex}.osm.pbf"
    try:
        with path.open("wb") as f:
            async for chunk in request.stream():
                f.write(chunk)
        if path.stat().st_size == 0:
            raise HTTPException(422, "Empty file")
        return await run_in_threadpool(_open, str(path), True, filename)
    except Exception:
        path.unlink(missing_ok=True)
        raise


@router.post("/technical/pbf/country")
async def country_pbf(body: CountryBody):
    """Builds (or returns the cached) boundaries of one country. The slow step; the levels returned are that country's own."""
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    return {
        "levels": data["levels"],
        "total": sum(x["count"] for x in data["levels"]),
        "fills": dict(data["fill"]),
    }


@router.post("/technical/pbf/preview")
async def preview_pbf(body: ExtractBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    pct = max(PREVIEW_PCT, body.simplify)
    content = await run_in_threadpool(_preview_body, data, _levels(body.levels), pct)
    return Response(content=content, media_type="application/json")


@router.post("/technical/pbf/rename")
async def rename_pbf(body: RenameBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    if body.level not in data["by_level"]:
        raise HTTPException(422, "That admin level is not in this country.")
    await run_in_threadpool(_rename, data, body.level, body.gap, body.name)
    return {"ok": True}


@router.post("/technical/pbf/fill")
async def fill_pbf(body: FillBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    if body.level not in data["by_level"]:
        raise HTTPException(422, "That admin level is not in this country.")
    if body.source is not None and (
        body.source == body.level or body.source not in data["by_level"]
    ):
        raise HTTPException(422, "Pick a different admin level to fill from.")
    await run_in_threadpool(_set_fill, data, body.level, body.source)
    return {"fills": dict(data["fill"])}


@router.post("/technical/pbf/merge")
async def merge_pbf(body: MergeBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    if body.level not in data["by_level"]:
        raise HTTPException(422, "That admin level is not in this country.")
    await run_in_threadpool(_merge, data, body.level, body.idxs)
    return {"ok": True}


@router.post("/technical/pbf/export")
async def export_pbf(body: ExtractBody):
    ds = _dataset(body.token)
    data = await run_in_threadpool(_load, ds, body.country)
    code = next((c["code"] for c in ds["countries"] if c["id"] == body.country), "")
    content, filename = await run_in_threadpool(
        _export, data, code or body.country, _levels(body.levels), body.simplify
    )
    return Response(
        content=content,
        media_type="application/topojson+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
