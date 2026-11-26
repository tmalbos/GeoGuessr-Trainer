"""pbf_admin.py — Technical tool: read a Geofabrik .osm.pbf, pick a country and export admin boundaries as TopoJSON.

Flow:
    POST /technical/pbf/open     {path}                       -> token + countries in the file
    POST /technical/pbf/upload   (raw body, ?filename=)       -> same, for a file picked in the browser
    POST /technical/pbf/country  {token, country}             -> SLOW, once per country: builds every admin boundary of that
                                                                 country, returns the levels it really has (+ counts, + fills)
    POST /technical/pbf/preview  {token, country, levels, simplify} -> per-level GeoJSON features (fast, cached)
    POST /technical/pbf/export   {token, country, levels, simplify, name, adm, parents} -> TopoJSON download
    POST /technical/pbf/parent-file (raw body, ?token=&filename=) -> imports a polygon file to use as a coarser layer
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
import os
import shutil
import tempfile
import threading
import time
from array import array
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, Response

from src.geo import osmium_tool, topojson_writer

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
# place=* values read as points (besides every relation's admin_centre / label node)
POINT_PLACES = frozenset(
    {"state", "province", "region", "county", "district", "municipality", "city", "town"}
)
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
_COAST: OrderedDict[str, dict] = (
    OrderedDict()
)  # geometry file -> its coastlines, read once (_coast_bundle)
MAX_COAST_FILES = 2
MIN_RING_PTS = 4  # a closed chain with fewer points than this is not a ring
MAX_SEA_LOG = 50  # removed sea parts reported per boundary
TILE_COORDS = (
    150_000  # a boundary with more vertices than twice this is cut in tiles of about this many
)
MAX_TILES_SIDE = 48  # at most this many tiles per side
TILE_WORKERS = max(1, min(8, os.cpu_count() or 2))  # threads for the tiles (GEOS releases the GIL)

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

        outer, inner, subs = array("q"), array("q"), array("q")
        centre = 0
        for m in obj.members:
            if str(m.type) in {"n", "node"}:
                if m.role == "admin_centre" or (m.role == "label" and not centre):
                    centre = m.ref
                continue
            if str(m.type) in {"r", "relation"}:  # sub-areas of this relation
                subs.append(m.ref)
                continue
            if str(m.type) not in {"w", "way"}:
                continue
            (inner if m.role == "inner" else outer).append(m.ref)
        if outer or centre or subs:
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
                "centre": centre,
                "subs": subs,
            }

    countries.sort(key=lambda c: c["name"].casefold())
    return countries, rels


def _geometry_file(path: Path) -> Path:
    """The file everything is read from: a small cached copy with the boundary=administrative relations, the
    natural=coastline ways, the place=* nodes and everything those reference (made by osmium-tool in ONE fast
    pass of the big file), or the original when osmium-tool isn't available. Python never touches the big file.
    """
    try:
        osmium_tool.require()
    except osmium_tool.OsmiumMissingError:
        return path
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:16]
    out = CACHE_DIR / f"admin-geo-{key}.osm.pbf"
    if out.is_file() and out.stat().st_size > 0:
        return out
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    part = CACHE_DIR / f"admin-geo-{key}.part.osm.pbf"
    try:
        osmium_tool.run(
            [
                "tags-filter",
                str(path),
                "r/boundary=administrative",
                "w/natural=coastline",
                "n/place=" + ",".join(sorted(POINT_PLACES)),
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
        geo_path = _geometry_file(path)
        countries, rels = _scan(geo_path)
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
        "geo_path": geo_path,
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


def _read_ways(
    path: Path,
    wanted: set[int],
    points: dict[int, dict] | None = None,
    centres: set[int] | frozenset = frozenset(),
) -> dict[int, np.ndarray]:
    """One pass over the file. Returns {way id: (n, 2) lon/lat array} for the wanted ways only.
    With `points` (a dict that gets filled), place=* nodes and the `centres` node ids are collected too.
    osmium filters out untagged nodes and unwanted ways before Python sees them (the node locations are still
    cached for the ways), so Python only touches the boundary ways.
    Ways with a missing node (extract edge) are skipped, and so are the relations that need them.
    """
    import osmium

    fp = osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY).with_locations()
    try:
        node_filter = (
            osmium.filter.KeyFilter("place")
            if points is not None
            else osmium.filter.EmptyTagFilter()
        )
        fp = fp.with_filter(node_filter.enable_for(osmium.osm.NODE))
        fp = fp.with_filter(osmium.filter.IdFilter(list(wanted)).enable_for(osmium.osm.WAY))
    except Exception:  # older pyosmium: slower, same result
        log.warning("osmium filters unavailable, reading without them", exc_info=True)

    try:
        factory = osmium.geom.WKBFactory()
    except AttributeError:  # very old pyosmium: the nodes are read one by one (slower, same result)
        factory = None
        log.warning("osmium.geom.WKBFactory unavailable, reading ways node by node")
    raw: list = []
    ids: list[int] = []
    for o in fp:
        if points is not None and o.is_node():
            place = o.tags.get("place")
            if o.id in centres or place in POINT_PLACES:
                points[o.id] = {
                    "lon": o.location.lon,
                    "lat": o.location.lat,
                    "name": o.tags.get("name:en") or o.tags.get("name") or "",
                    "place": place or "",
                }
            continue
        if not o.is_way() or o.id not in wanted:
            continue
        try:
            if factory is not None:
                geom = factory.create_linestring(o)  # C++; raises when a node location is unknown
            else:
                xy = np.array([(n.lon, n.lat) for n in o.nodes], dtype=float)
                if len(xy) < 2:
                    continue
                geom = shapely.linestrings(xy)
        except Exception:  # noqa: BLE001  # a node location is unknown, or fewer than two distinct nodes
            continue
        raw.append(geom)
        ids.append(o.id)
    return _way_arrays(raw, ids, factory is not None)


def _way_arrays(raw: list, ids: list[int], wkb: bool) -> dict[int, np.ndarray]:
    """{way id: (n, 2) lon/lat array} from the geometries collected by _read_ways, in one vectorised step."""
    if not raw:
        return {}
    if wkb:
        arr = shapely.from_wkb(np.array(raw, dtype=object))
    else:
        arr = np.empty(len(raw), dtype=object)
        arr[:] = raw
    xy, owner = shapely.get_coordinates(arr, return_index=True)
    parts = np.split(xy, np.flatnonzero(np.diff(owner)) + 1)  # every way has at least two points
    return dict(zip(ids, parts, strict=True))


def _read_coast(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """One pass over the file. Returns EVERY natural=coastline way as a directed LineString (land is on its left)
    and the OSM id of each way. osmium (C++) builds the geometry and hands it over as WKB, so Python never loops
    over the nodes. The result is kept per file (see _coast_bundle), so it is read once, not once per country.
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
    try:
        factory = osmium.geom.WKBFactory()
    except (
        AttributeError
    ):  # very old pyosmium: the lines are built node by node (slower, same result)
        factory = None
        log.warning("osmium.geom.WKBFactory unavailable, building coastlines in Python")

    raw: list = []
    way_ids: list = []
    for o in fp:
        if not o.is_way() or o.tags.get("natural") != "coastline":
            continue
        try:
            if factory is not None:
                geom = factory.create_linestring(o)
            else:
                xy = np.array([(n.lon, n.lat) for n in o.nodes], dtype=float)
                if len(xy) < 2:
                    continue
                geom = shapely.linestrings(xy)
        except Exception:  # noqa: BLE001  # a node location is unknown, or fewer than two distinct nodes
            continue
        raw.append(geom)
        way_ids.append(o.id)
    if factory is not None and raw:
        arr = shapely.from_wkb(np.array(raw, dtype=object))
    else:
        arr = np.empty(len(raw), dtype=object)
        arr[:] = raw
    return arr, np.array(way_ids, dtype=np.int64)


def _split_rings(ways: np.ndarray) -> dict | None:
    """Sorts the coastline ways into closed rings and open chains, once per file.

    The ways are chained end to end WITHOUT changing their direction. A chain that closes on itself is an island
    (or a hole): OSM keeps land on the LEFT, so a counter-clockwise ring is land inside, a clockwise one is sea
    inside. That tells land from sea without noding or ray casting. Only the open chains (cut by the edge of the
    extract) and the rare ring that is not a valid polygon still need the slow noded cut.

    Returns {polys, pts, ccw, parent, children, tree, chains, chain_tree} or None when it can't be done (then the
    noded cut is used for everything). `parent[i]` is the smallest ring that contains ring i (-1 = none) and
    `children[i]` the rings directly inside it.
    """
    try:
        merged = shapely.get_parts(
            shapely.line_merge(shapely.multilinestrings(ways), directed=True)
        )
    except Exception:  # directed merging needs GEOS >= 3.11
        log.warning("Could not chain the coastlines into rings; using the noded cut", exc_info=True)
        return None
    shut = shapely.is_closed(merged) & (shapely.get_num_coordinates(merged) >= MIN_RING_PTS)
    cand = merged[shut]
    polys = np.empty(0, dtype=object)
    ccw = np.empty(0, dtype=bool)
    chains = merged[~shut]
    if len(cand):
        xy, idx = shapely.get_coordinates(cand, return_index=True)
        rings = shapely.linearrings(xy, indices=idx)
        polys = shapely.polygons(rings)
        good = np.asarray(shapely.is_valid(polys), dtype=bool)
        ccw = np.asarray(shapely.is_ccw(rings[good]), dtype=bool)
        polys = polys[good]
        chains = np.concatenate([chains, cand[~good]])

    parent = np.full(len(polys), -1, dtype=np.int64)
    children: dict[int, list[int]] = {}
    pts = shapely.point_on_surface(polys) if len(polys) else polys
    tree = shapely.STRtree(polys) if len(polys) else None
    if len(polys):
        ii, jj = tree.query(pts, predicate="within")
        keep = ii != jj
        ii, jj = ii[keep], jj[keep]
        area = shapely.area(polys)
        order = np.lexsort((area[jj], ii))  # per ring, its smallest container first
        ii, jj = ii[order], jj[order]
        first = np.ones(len(ii), dtype=bool)
        first[1:] = ii[1:] != ii[:-1]
        parent[ii[first]] = jj[first]
        for i, p in zip(ii[first].tolist(), jj[first].tolist(), strict=True):
            children.setdefault(p, []).append(i)
    return {
        "polys": polys,
        "pts": pts,
        "ccw": ccw,
        "parent": parent,
        "children": children,
        "tree": tree,
        "chains": chains,
        "chain_tree": shapely.STRtree(chains) if len(chains) else None,
    }


def _coast_bundle(path: Path) -> dict:
    """The coastlines of a geometry file, read and sorted once and kept (the last MAX_COAST_FILES files).
    Caller holds _LAND.
    """
    key = f"{path.resolve()}|{path.stat().st_mtime_ns}"
    hit = _COAST.get(key)
    if hit is not None:
        _COAST.move_to_end(key)
        return hit
    t0 = time.perf_counter()
    ways, ids = _read_coast(path)
    t1 = time.perf_counter()
    rings = _split_rings(ways) if len(ways) else None
    t2 = time.perf_counter()
    hit = {
        "ways": ways,
        "ids": ids,
        "tree": shapely.STRtree(ways) if len(ways) else None,
        "rings": rings,
    }
    log.info(
        "Coastlines: %d ways read in %.1fs; %s (%.1fs)",
        len(ways),
        t1 - t0,
        "no ring split"
        if rings is None
        else f"{len(rings['polys'])} closed rings, {len(rings['chains'])} open chains",
        t2 - t1,
    )
    _COAST[key] = hit
    while len(_COAST) > MAX_COAST_FILES:
        _COAST.popitem(last=False)
    return hit


def _faces(way_ids, ways: dict[int, np.ndarray]):
    """Filled polygon(s) of a set of ways. Fast path: the ways chain end to end into closed rings (the normal case)
    and no noding is needed. Otherwise the slower polygonize path.
    """
    lines = [shapely.linestrings(ways[i]) for i in way_ids if i in ways]
    if not lines:
        return None
    parts = shapely.get_parts(shapely.line_merge(shapely.multilinestrings(lines)))
    closed = shapely.is_closed(parts) & (shapely.get_num_coordinates(parts) >= 4)
    polys = [shapely.Polygon(p.coords) for p in parts[closed]]
    if not closed.all():  # only the ways that don't chain into rings need noding + polygonize
        loose = shapely.line_merge(shapely.union_all(parts[~closed]))
        polys += [
            p
            for p in shapely.get_parts(shapely.polygonize(shapely.get_parts(loose)))
            if p.geom_type == "Polygon" and not p.is_empty
        ]
    if len(polys) < 2:
        return polys[0] if polys else None
    arr = np.empty(len(polys), dtype=object)
    arr[:] = polys
    bad = ~shapely.is_valid(arr)
    arr[bad] = shapely.make_valid(arr[bad])
    return shapely.union_all(arr)


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


def _build_points(rels: dict[int, dict], polygons: set[int], nodes: dict[int, dict]) -> dict:
    """Points of the file: one per admin relation (its admin_centre / label node, exact level, `polygon` says
    whether the relation also produced a polygon) and the other place=* nodes (level unknown).
    """
    relation, used = [], set()
    for rid, r in rels.items():
        n = nodes.get(r.get("centre"))
        if n is None:
            continue
        used.add(r["centre"])
        relation.append(
            {
                "name": r["name"],
                "admin_level": r["level"],
                "osm_id": str(rid),
                "iso": r["iso"],
                "source": "relation",
                "polygon": rid in polygons,
                "lon": n["lon"],
                "lat": n["lat"],
            }
        )
    place = [
        {
            "name": n["name"],
            "admin_level": None,
            "place": n["place"],
            "osm_id": str(i),
            "source": "place",
            "lon": n["lon"],
            "lat": n["lat"],
        }
        for i, n in nodes.items()
        if i not in used and n["place"] in POINT_PLACES
    ]
    return {"relation": relation, "place": place}


COUNTRY_SLACK_DEG = 0.01  # ~1 km: a boundary on the coast still counts as inside the country


COUNTRY_PAD = 1.0  # degrees of the file kept around a country when it gets a file of its own


def _country_file(ds: dict, country: str) -> Path:
    """The geometry file to read for one country. A file that holds several countries is cut to the country's
    bounding box by osmium-tool (C++, complete ways) once and cached on disk, so Python only reads that country's
    boundaries and coastlines. Falls back to the whole geometry file whenever that can't be done.
    """
    geo_path: Path = ds["geo_path"]
    rel = ds["rels"].get(int(country)) if country.isdigit() else None
    if rel is None or len(ds["countries"]) < 2:
        return geo_path
    st = geo_path.stat()
    raw = f"{geo_path.resolve()}|{st.st_size}|{st.st_mtime_ns}|{country}"
    key = hashlib.sha1(raw.encode()).hexdigest()[:16]
    out = CACHE_DIR / f"country-{key}.osm.pbf"
    if out.is_file() and out.stat().st_size > 0:
        return out
    try:
        osmium_tool.require()
    except osmium_tool.OsmiumMissingError:
        return geo_path
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="country-", dir=CACHE_DIR))
    try:
        ids = work / "ids.txt"
        ids.write_text(f"r{country}\n", encoding="utf-8")
        outline = work / "outline.osm.pbf"
        osmium_tool.run(
            ["getid", "-r", "-i", str(ids), str(geo_path), "--output", str(outline), "--overwrite"]
        )
        found = _read_ways(outline, {*rel["outer"], *rel["inner"]})
        if not found:
            return geo_path
        pts = np.vstack(list(found.values()))
        w, s = (float(v) - COUNTRY_PAD for v in pts.min(axis=0))
        e, n = (float(v) + COUNTRY_PAD for v in pts.max(axis=0))
        if e - w > 300:  # crosses the antimeridian: nothing to cut away
            return geo_path
        box = f"{max(w, -180.0)},{max(s, -90.0)},{min(e, 180.0)},{min(n, 90.0)}"
        part = CACHE_DIR / f"country-{key}.part.osm.pbf"
        osmium_tool.run(
            [
                "extract",
                "--bbox",
                box,
                "--strategy",
                "complete_ways",
                "--output",
                str(part),
                "--overwrite",
                str(geo_path),
            ]
        )
        part.replace(out)
    except (osmium_tool.OsmiumError, OSError):
        log.warning(
            "Could not cut a file for country %s; using the whole file", country, exc_info=True
        )
        return geo_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def _geo_data(ds: dict, path: Path) -> dict:
    """Ways (lon/lat arrays), their bounding boxes and the point nodes of the geometry file `path` (the whole file or
    a country's own, see _country_file). Read once and kept while the same file is asked for. Caller holds _COMPUTE.
    """
    hit = ds.get("geo")
    if hit is None or hit["path"] != path:
        rels = ds["rels"]
        wanted: set[int] = set()
        for r in rels.values():
            wanted.update(r["outer"])
            wanted.update(r["inner"])
        centres = {r["centre"] for r in rels.values() if r.get("centre")}
        nodes: dict[int, dict] = {}
        ways = _read_ways(path, wanted, nodes, centres)
        boxes = {
            i: (xy[:, 0].min(), xy[:, 1].min(), xy[:, 0].max(), xy[:, 1].max())
            for i, xy in ways.items()
        }
        hit = ds["geo"] = {"path": path, "ways": ways, "nodes": nodes, "boxes": boxes}
    return hit


def _descendants(rels: dict[int, dict], root: int) -> set[int]:
    """The relation and every relation reachable through its sub-area members."""
    seen = {root}
    stack = [root]
    while stack:
        for sub in rels[stack.pop()].get("subs", ()):
            if sub in rels and sub not in seen:
                seen.add(sub)
                stack.append(sub)
    return seen


def _extract(ds: dict, country: str) -> dict:
    country_name = next((c["name"] for c in ds["countries"] if c["id"] == country), None)
    if country_name is None:
        raise HTTPException(404, "That country is no longer in the open file.")

    rels: dict[int, dict] = ds["rels"]
    path = _country_file(ds, country)
    geo = _geo_data(ds, path)
    ways, nodes, boxes = geo["ways"], geo["nodes"], geo["boxes"]
    if not ways:
        raise HTTPException(422, "No boundary geometry could be read from this file.")

    # Only this country is built: its own boundary first, then its sub-areas and every boundary that lies inside it.
    # When the country's own boundary is incomplete in the file, everything is built (as before).
    cid = int(country) if country.isdigit() else None
    country_geom = _assemble(rels[cid], ways) if cid in rels else None
    scope = wide = None
    assembled: dict[int, object] = {}
    if country_geom is not None:
        assembled[cid] = country_geom
        scope = _descendants(rels, cid)
        w, s, e, n = country_geom.bounds
        wide = shapely.buffer(country_geom, COUNTRY_SLACK_DEG)
        shapely.prepare(wide)
        for rid, r in rels.items():
            if rid in assembled:
                continue
            listed = rid in scope
            if not listed:
                bs = [boxes[i] for i in r["outer"] if i in boxes]
                if not bs or (
                    min(b[0] for b in bs) > e
                    or max(b[2] for b in bs) < w
                    or min(b[1] for b in bs) > n
                    or max(b[3] for b in bs) < s
                ):
                    continue
            g = _assemble(r, ways)
            if g is None:
                continue
            if not listed and not shapely.intersects(wide, shapely.point_on_surface(g)):
                continue
            assembled[rid] = g
    else:
        for rid, r in rels.items():
            g = _assemble(r, ways)
            if g is not None:
                assembled[rid] = g
    ids = list(assembled)
    geoms = list(assembled.values())
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

    country_geom = assembled.get(cid)

    pts_rels, pts_nodes = rels, nodes
    if scope is not None:
        pts_rels = {i: rels[i] for i in scope | set(ids)}
        keep = {r["centre"] for r in pts_rels.values() if r.get("centre")}
        inside = shapely.contains_xy(
            wide,
            np.array([v["lon"] for v in nodes.values()], dtype=float),
            np.array([v["lat"] for v in nodes.values()], dtype=float),
        )
        pts_nodes = {
            i: v for (i, v), ok in zip(nodes.items(), inside, strict=True) if ok or i in keep
        }

    return {
        "country": country_name,
        "points": _build_points(pts_rels, set(ids), pts_nodes),
        "by_level": by_level,
        "levels": [{"level": level, "count": len(v["props"])} for level, v in by_level.items()],
        "diag": max(math.hypot(cmaxx - cminx, cmaxy - cminy), 1e-9),
        "path": path,
        "coast_loaded": False,  # coastlines are read the first time a level is finished
        "coast": None,
        "coast_ids": None,
        "tree": None,
        "rings": None,  # closed coastline rings sorted by direction (see _split_rings)
        "cbox": cbox,
        "country_geom": country_geom,  # the selected country's own boundary (None when incomplete in the file)
        "country_land": None,  # ...cut to land, computed together with the coastlines
        "country_land_ready": False,
        "trees": {},  # level -> STRtree of the raw boundaries (for the gap filling)
        "land": {},  # level -> {"props", "geoms"}: finished (cut to land + gaps filled)
        "fill": {},  # level -> other level whose polygons fill its gaps (absent = "Unnamed N")
        "renames": {},  # level -> {gap number: custom name}
        "undo": [],
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


def _ring_key(ring) -> bytes:
    return shapely.to_wkb(shapely.normalize(shapely.Polygon(ring)))


def _classify(
    faces: list, coast: np.ndarray, tree, cbox, known: dict[int, bool] | None = None
) -> list[tuple[bool | None, int | None]]:
    """(land, deciding coastline) for every face of a coastline split.

    Crossing a coastline flips land / sea, and an island is a hole of the sea face around it, so the ring of a hole is
    the outer ring of exactly one other face. Faces are therefore arranged in a tree by that ring, only the roots
    (faces that are not a hole of another face) are tested with _is_land, and every child is the opposite of its
    parent. Faces that match nothing are roots, so they are still tested one by one.
    `known` = {face index: is land} for roots whose answer is already known (they are not tested).
    """
    own = {_ring_key(f.exterior): i for i, f in enumerate(faces)}
    kids: list[list[int]] = [[] for _ in faces]
    child: set[int] = set()
    for i, f in enumerate(faces):
        for ring in f.interiors:
            j = own.get(_ring_key(ring))
            if j is not None and j != i and j not in child:
                kids[i].append(j)
                child.add(j)
    out: list[tuple[bool | None, int | None]] = [(None, None)] * len(faces)
    for root in (i for i in range(len(faces)) if i not in child):
        if known is not None and root in known:
            land, way = known[root], None
        else:
            pt = shapely.point_on_surface(faces[root])
            land, way = _is_land(pt.x, pt.y, coast, tree, cbox)
        stack = [(root, land)]
        while stack:
            i, v = stack.pop()
            out[i] = (v, way)
            stack.extend((j, None if v is None else not v) for j in kids[i])
    return out


def _land_only_noded(g, coast: np.ndarray, tree, cbox) -> tuple:
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
    for q, (is_land, way) in zip(parts, _classify(parts, coast, tree, cbox), strict=True):
        if is_land is False:
            pt = shapely.point_on_surface(q)
            sea.append((q.area, pt.y, pt.x, way))
        else:
            land.append(q)
    if not sea:
        return g, sea, gaps
    return (_polys(shapely.coverage_union_all(land)) if land else None), sea, gaps


def _ring_lines(polys: np.ndarray) -> np.ndarray:
    """The outlines of ring polygons as LineStrings (what the noding step takes)."""
    out = np.empty(len(polys), dtype=object)
    if len(polys):
        xy, idx = shapely.get_coordinates(shapely.get_exterior_ring(polys), return_index=True)
        out[:] = shapely.linestrings(xy, indices=idx)
    return out


def _land_only_rings(g, coast: np.ndarray, tree, cbox, R: dict):
    """Same result as _land_only_noded, but the coastlines that cannot cross `g` are never noded.

    A closed coastline ring that lies entirely inside `g` is land (counter-clockwise) or sea (clockwise) inside,
    and the other way round just outside, so it is simply a hole or an island of the face around it. Only the open
    chains and the rings that touch / cross / contain `g` go through noding + polygonize. The faces that come out
    are then given their rings as holes (land faces) or as islands (sea faces) by plain assembly, with no boolean
    operation. Returns None when something doesn't add up (the caller then uses the noded cut).
    """
    shapely.prepare(g)
    polys, parent, children, ccw = R["polys"], R["parent"], R["children"], R["ccw"]
    none = np.empty(0, dtype=np.intp)
    hit = R["tree"].query(g, predicate="intersects") if len(polys) else none
    if len(hit):
        proper = np.asarray(shapely.contains_properly(g, polys[hit]), dtype=bool)
        inside, touching = hit[proper], hit[~proper]
    else:
        inside = touching = none
    chain_hit = (
        R["chain_tree"].query(g, predicate="intersects") if R["chain_tree"] is not None else none
    )
    inside_set = set(inside.tolist())
    tops = [
        i for i in inside.tolist() if int(parent[i]) not in inside_set
    ]  # not inside another ring of `g`

    gaps: list = []
    faces: list = []
    if len(touching) or len(chain_hit):
        w, s_, e, n = g.bounds
        margin = 0.1
        lines = shapely.clip_by_rect(
            shapely.multilinestrings(
                np.concatenate([R["chains"][chain_hit], _ring_lines(polys[touching])])
            ),
            w - margin,
            s_ - margin,
            e + margin,
            n + margin,
        )
        noded = shapely.union_all([shapely.boundary(g), lines])
        poly_c, _cuts, dangles, _bad = shapely.polygonize_full(shapely.get_parts(noded))
        found = [
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
        if found:
            farr = np.empty(len(found), dtype=object)
            farr[:] = found
            farr = farr[np.asarray(shapely.covers(g, shapely.point_on_surface(farr)), dtype=bool)]
            faces = list(farr)
    if not faces:
        faces = [q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and not q.is_empty]

    # every top-level ring goes to the face that holds it
    owner: dict[int, int] = {}
    if tops and len(faces) == 1:
        owner = dict.fromkeys(tops, 0)
    elif tops:
        farr = np.empty(len(faces), dtype=object)
        farr[:] = faces
        top_pts = R["pts"][np.asarray(tops)]
        ti, fj = shapely.STRtree(farr).query(top_pts)  # bounding boxes only
        px, py = shapely.get_x(top_pts), shapely.get_y(top_pts)
        for f in np.unique(fj).tolist():
            idx = ti[fj == f]
            shapely.prepare(
                farr[f]
            )  # indexed point-in-polygon: a face can have millions of vertices
            for a in idx[shapely.contains_xy(farr[f], px[idx], py[idx])].tolist():
                owner.setdefault(tops[a], f)
        if len(owner) < len(tops):
            return None
    by_face: dict[int, list[int]] = {}
    for t in tops:
        by_face.setdefault(owner[t], []).append(t)
    # A face that holds rings is land or sea by the direction of its first ring (its inside is the opposite). The
    # point of such a face may lie inside an island, so it is not ray cast.
    known = {fi: not bool(ccw[mine[0]]) for fi, mine in by_face.items()}
    states = _classify(faces, coast, tree, cbox, known)

    base: list = []
    islands: list = []
    sea: list = []
    assembled: list = []  # polygons that got extra holes: checked below
    for fi, f in enumerate(faces):
        is_land, way = states[fi]
        land = is_land is not False
        mine = by_face.get(fi, [])
        if land:
            if mine:
                f = shapely.Polygon(f.exterior, [*f.interiors, *(polys[t].exterior for t in mine)])
                assembled.append(f)
            base.append(f)
        else:
            pt = shapely.point_on_surface(f)
            sea.append((f.area, pt.y, pt.x, way))
        stack = [(t, not land) for t in mine]  # (ring, is its inside land)
        while stack:
            k, inner_land = stack.pop()
            kids = children.get(k, ())
            if inner_land:
                if kids:
                    ring = shapely.Polygon(polys[k].exterior, [polys[c].exterior for c in kids])
                    assembled.append(ring)
                    islands.append(ring)
                else:
                    islands.append(polys[k])
            elif len(sea) < MAX_SEA_LOG:
                pt = R["pts"][k]
                sea.append((float(shapely.area(polys[k])), pt.y, pt.x, None))
            stack.extend((c, not inner_land) for c in kids)
    if assembled and not np.asarray(shapely.is_valid(assembled), dtype=bool).all():
        return None  # rings that touch each other can make a hole layout that is not valid
    if not sea:
        return g, sea, gaps
    if len(base) > 1:
        base = list(shapely.get_parts(shapely.coverage_union_all(base)))
    out = [q for q in [*base, *islands] if q.geom_type == "Polygon" and not q.is_empty]
    if not out:
        return None, sea, gaps
    return (out[0] if len(out) == 1 else shapely.multipolygons(out)), sea, gaps


def _land_only(
    g, coast: np.ndarray, tree, cbox, rings: dict | None = None, tiled: bool = True
) -> tuple:
    """(land part of boundary `g` or None when none of it is land, removed sea parts, coastline gaps).
    With the coastline rings of the file (see _split_rings) the closed rings are read by their direction and only
    the open chains are noded; without them (or when that can't be done) everything goes through the noded cut.
    A boundary with very many vertices is first cut in tiles (see _land_only_tiled).
    """
    if tiled:
        try:
            res = _land_only_tiled(g, coast, tree, cbox, rings)
        except Exception:
            log.warning("Tiled coast cut failed, cutting in one piece", exc_info=True)
            res = None
        if res is not None:
            return res
    if rings is not None:
        try:
            res = _land_only_rings(g, coast, tree, cbox, rings)
        except Exception:
            log.warning("Ring based coast cut failed, using the noded cut", exc_info=True)
            res = None
        if res is not None:
            return res
    return _land_only_noded(g, coast, tree, cbox)


# ── tiles: big boundaries are cut in a grid so no single operation sees millions of vertices ──


def _grid(bounds, n_coords: int) -> tuple[np.ndarray, np.ndarray]:
    """Grid lines (x, y) that split `bounds` into about TILE_COORDS vertices per tile."""
    w, s, e, n = bounds
    side = int(min(MAX_TILES_SIDE, max(1, math.ceil(math.sqrt(n_coords / TILE_COORDS)))))
    return np.linspace(w, e, side + 1), np.linspace(s, n, side + 1)


def _pool_map(fn, items) -> list:
    """Fn over items on a few threads (GEOS releases the GIL). Errors are raised here."""
    items = list(items)
    if len(items) < 2 or TILE_WORKERS < 2:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(len(items), TILE_WORKERS)) as pool:
        return list(pool.map(fn, items))


def _join(pieces: list):
    """One polygonal geometry from pieces that only meet along tile edges (None when nothing is left).
    The pieces share those edges exactly, so a coverage union is enough; if the result is not what a coverage
    union of these pieces should be, the slow general union is used instead.
    """
    pieces = [p for p in pieces if p is not None and not p.is_empty]
    if not pieces:
        return None
    if len(pieces) == 1:
        return pieces[0]
    try:
        u = shapely.coverage_union_all(pieces)
        total = float(shapely.area(pieces).sum())
        if shapely.is_valid(u) and shapely.area(u) >= (1 - 1e-9) * total:
            return _polys(u)
    except Exception:
        log.warning("Coverage union of tiles failed, using the general union", exc_info=True)
    return _polys(shapely.union_all(pieces))


def _tile_range(lo: float, hi: float, lines: np.ndarray) -> range:
    """Indices of the grid cells (between consecutive `lines`) that [lo, hi] touches."""
    last = len(lines) - 2
    a = int(np.clip(np.searchsorted(lines, lo, "right") - 1, 0, last))
    b = int(np.clip(np.searchsorted(lines, hi, "left") - 1, a, last))
    return range(a, b + 1)


def _land_only_tiled(g, coast: np.ndarray, tree, cbox, rings):
    """_land_only for a boundary with too many vertices: every grid tile is cut on its own (in parallel) and the
    land pieces are joined along the tile edges. None when `g` is small enough not to need it.
    """
    nv = int(shapely.get_num_coordinates(g))
    if nv < 2 * TILE_COORDS:
        return None
    xs, ys = _grid(g.bounds, nv)
    cells = [
        (xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(len(xs) - 1) for j in range(len(ys) - 1)
    ]

    def one(t):
        gt = _polys(shapely.clip_by_rect(g, *t))
        return None if gt is None else _land_only(gt, coast, tree, cbox, rings, tiled=False)

    pieces, sea, gaps = [], [], []
    for res in _pool_map(one, cells):
        if res is None:
            continue
        land, s, gp = res
        if land is not None:
            pieces.append(land)
        sea.extend(s)
        gaps.extend(gp)
    gaps = sorted(gaps, reverse=True)[:5]
    if not sea:
        return g, [], gaps
    return _join(pieces), sea[:MAX_SEA_LOG], gaps


def _land_grid(data: dict, land) -> dict:
    """The land cut into the cells of a grid (cached): {xs, ys, tiles: {(i, j): land inside that cell}}."""
    hit = data.get("land_grid")
    if hit is not None and hit["land"] is land:
        return hit
    parts = np.array(
        [p for p in shapely.get_parts(land) if p.geom_type == "Polygon" and not p.is_empty],
        dtype=object,
    )
    xs, ys = _grid(land.bounds, int(shapely.get_num_coordinates(land)))
    ptree = shapely.STRtree(parts)
    cells = [(i, j) for i in range(len(xs) - 1) for j in range(len(ys) - 1)]

    def clip(ij):
        i, j = ij
        rect = (xs[i], ys[j], xs[i + 1], ys[j + 1])
        idx = ptree.query(shapely.box(*rect))
        if not len(idx):
            return None
        out = [
            q
            for c in shapely.clip_by_rect(parts[idx], *rect)
            for q in shapely.get_parts(c)
            if q.geom_type == "Polygon" and not q.is_empty
        ]
        return None if not out else out[0] if len(out) == 1 else shapely.multipolygons(out)

    tiles = {ij: c for ij, c in zip(cells, _pool_map(clip, cells), strict=True) if c is not None}
    hit = data["land_grid"] = {"land": land, "xs": xs, "ys": ys, "tiles": tiles}
    return hit


def _cut_to_land(geoms: np.ndarray, land, data: dict) -> np.ndarray:
    """intersection(geom, land) for every geom, tile by tile: a geom only meets the part of the land that lies in
    the tiles it touches, and the work is spread over threads. Same result as the plain intersection.
    """
    grid = _land_grid(data, land)
    xs, ys, tiles = grid["xs"], grid["ys"], grid["tiles"]

    def one(g):
        try:
            w, s, e, n = g.bounds
            pieces = []
            for i in _tile_range(w, e, xs):
                for j in _tile_range(s, n, ys):
                    lc = tiles.get((i, j))
                    if lc is None:
                        continue
                    rect = (xs[i], ys[j], xs[i + 1], ys[j + 1])
                    inside = rect[0] <= w and e <= rect[2] and rect[1] <= s and n <= rect[3]
                    gc = g if inside else shapely.clip_by_rect(g, *rect)
                    if gc.is_empty:
                        continue
                    pieces.append(_polys(_safe(shapely.intersection, gc, lc)))
            return _join(pieces)
        except Exception:
            log.warning("Tiled land cut failed, using the plain intersection", exc_info=True)
            return _polys(_safe(shapely.intersection, g, land))

    out = np.empty(len(geoms), dtype=object)
    out[:] = _pool_map(one, geoms)
    return out


def _tiles_exactly(geoms, area: float) -> bool:
    """True when `geoms` add up to exactly `area` (within MIN_PIECE). Level polygons are cut to the same land, so
    when nothing is left over and nothing overlaps their areas sum to the land's area. Overlaps or gaps change
    the sum, so a "no" only costs the overlay it would have needed anyway.
    """
    return abs(float(shapely.area(geoms).sum()) - area) <= MIN_PIECE


def _gaps(country, geoms: list):
    """What of `country` (land) no geometry of `geoms` covers: country minus union(geoms), done tile by tile when
    the country is big (the union of a whole level is one of the slowest operations there is).
    """
    if not geoms:
        return country
    nv = int(shapely.get_num_coordinates(country))
    if _tiles_exactly(geoms, country.area):
        return None
    if nv < 2 * TILE_COORDS:
        return _safe(shapely.difference, country, shapely.union_all(geoms))
    xs, ys = _grid(country.bounds, nv)
    cells = [
        (xs[i], ys[j], xs[i + 1], ys[j + 1]) for i in range(len(xs) - 1) for j in range(len(ys) - 1)
    ]
    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    gtree = shapely.STRtree(arr)

    def one(t):
        cc = shapely.clip_by_rect(country, *t)
        if cc.is_empty:
            return None
        idx = gtree.query(shapely.box(*t))
        if not len(idx):
            return cc
        pieces = shapely.clip_by_rect(arr[idx], *t)
        if _tiles_exactly(pieces, cc.area):
            return None  # the level covers this tile completely: no overlay needed
        return _safe(shapely.difference, cc, shapely.union_all(pieces))

    pieces = [
        q
        for r in _pool_map(one, cells)
        if r is not None
        for q in shapely.get_parts(r)
        if q.geom_type == "Polygon" and not q.is_empty
    ]
    return _polys(shapely.union_all(pieces)) if pieces else None


def _cut_level(data: dict, level: int) -> dict:
    """_cut_level_raw, remembered. The single-polygon reference level is cut for itself and again when a finer
    level needs its land, and the cut is by far the slowest step. Caller holds _LAND.
    """
    cache = data.setdefault("cut_cache", {})
    if level not in cache:
        cache[level] = _cut_level_raw(data, level)
    return cache[level]


def _cut_level_raw(data: dict, level: int) -> dict:
    """One level's boundaries cut to land. Boundaries that are entirely sea are dropped. Caller holds _LAND."""
    lv = data["by_level"][level]
    if data["tree"] is None:
        return lv
    ref = min((k for k, v in data["by_level"].items() if len(v["geoms"]) == 1), default=None)
    if ref is not None and level > ref:
        if (
            "ref_land" not in data
        ):  # the coastline cut of the single-polygon reference level, done once
            r = _cut_level(data, ref)
            data["ref_land"] = r["geoms"][0] if len(r["geoms"]) else None
        land = data["ref_land"]
        if land is not None:
            shapely.prepare(land)
            out = lv["geoms"].copy()
            edge = ~shapely.covers(land, out)  # polygons fully inside the land need no cut
            if edge.any():
                out[edge] = _cut_to_land(out[edge], land, data)
            keep = [(p, _polys(g)) for p, g in zip(lv["props"], out, strict=True)]
            keep = [(p, g) for p, g in keep if g is not None]
            arr = np.empty(len(keep), dtype=object)
            arr[:] = [g for _, g in keep]
            return {"props": [p for p, _ in keep], "geoms": arr}
    props, geoms = [], []
    t0 = time.perf_counter()
    for p, g in zip(lv["props"], lv["geoms"], strict=True):
        try:
            ng, sea, gaps = _land_only(
                g, data["coast"], data["tree"], data["cbox"], data.get("rings")
            )
        except Exception:
            log.warning("Coastline cut failed for %s", p["name"], exc_info=True)
            ng, sea, gaps = g, [], []
        data.setdefault("cut", {})[id(g)] = (g, ng)
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
                    data["coast_ids"][way] if way is not None else "ring direction",
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
    log.info(
        "Coast cut ADM%d: %d boundaries in %.1fs", level, len(lv["geoms"]), time.perf_counter() - t0
    )
    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    return {"props": props, "geoms": arr}


def _ensure_coast(data: dict) -> None:
    """Pick up the coastlines of the file (read and sorted once per file, see _coast_bundle) the first time a
    level is finished.
    """
    with _LAND:
        if data["coast_loaded"]:
            return
        try:
            bundle = _coast_bundle(data["path"])
            data["coast"], data["coast_ids"] = bundle["ways"], bundle["ids"]
            data["tree"], data["rings"] = bundle["tree"], bundle["rings"]
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
            done = data.get("cut", {}).get(id(g))
            try:
                if done is not None and done[0] is g:
                    g = done[1]
                else:
                    g = _land_only(g, data["coast"], data["tree"], data["cbox"], data.get("rings"))[
                        0
                    ]
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


def _neighbours(pieces: np.ndarray, geoms: np.ndarray, tree) -> np.ndarray:
    """Per gap piece, the index of the closest boundary, touching it or not (an island has nothing to touch); -1 when
    there is none. Ties (several boundaries touching it) go to the one closest to the piece's centre.
    """
    out = np.full(len(pieces), -1, dtype=np.int64)
    pair = tree.query_nearest(pieces, all_matches=True)
    if pair.shape[1]:
        dist = shapely.distance(shapely.centroid(pieces[pair[0]]), geoms[pair[1]])
        order = np.lexsort((dist, pair[0]))
        who = pair[0][order]
        first = order[np.r_[True, who[1:] != who[:-1]]]
        out[pair[0][first]] = pair[1][first]
    return out


def _nearest_by_vertex(pieces: np.ndarray, geoms: np.ndarray) -> np.ndarray | None:
    """Index of the polygon of `geoms` closest to each piece, by nearest vertex (a KD-tree, so no distance is ever
    measured against a polygon with millions of vertices). Coastlines are densely sampled, so the closest vertex
    belongs to the closest polygon. None when scipy is missing; the caller then uses _neighbours.
    """
    try:
        from scipy.spatial import cKDTree
    except ImportError:  # pragma: no cover
        return None
    xy, owner = shapely.get_coordinates(geoms, return_index=True)
    if not len(xy):
        return None
    scale = np.array(
        [math.cos(math.radians(float(xy[:, 1].mean()))) or 1e-6, 1.0]
    )  # lon degrees shrink with lat
    _, idx = cKDTree(xy * scale).query(
        shapely.get_coordinates(shapely.point_on_surface(pieces)) * scale
    )
    return owner[idx]


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
    gap = _gaps(country, geoms)
    if gap is None or gap.is_empty:
        return base
    pieces = [q for q in shapely.get_parts(gap) if q.geom_type == "Polygon" and not q.is_empty]
    if not pieces:
        return base

    small = SMALL_SHARE * country.area
    edge = shapely.boundary(country)
    parr = np.empty(len(pieces), dtype=object)
    parr[:] = pieces
    frag = shapely.area(parr) < small
    if frag.any():
        shapely.prepare(edge)  # indexed: thousands of fragments against a huge outline
        frag[frag] = shapely.dwithin(edge, parr[frag], TOUCH_TOL)
    fragments: list = list(parr[frag])
    rest: list = list(parr[~frag])

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
    if gap is not None:  # `gap` is inside `country` land, so it cannot be sea
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
        oarr = np.array(out, dtype=object)
        farr = np.array(fragments, dtype=object)
        near = _nearest_by_vertex(farr, oarr)
        if near is None:
            near = _neighbours(farr, oarr, shapely.STRtree(oarr))
        for q, i in zip(fragments, near.tolist(), strict=True):
            if i >= 0:
                merged.setdefault(i, []).append(q)
        for i, qs in merged.items():
            g = _polys(_safe(shapely.union, out[i], shapely.union_all(qs)))
            if g is not None:
                out[i] = g

    log.info(
        "Gap fill ADM%d: %d fragment(s) merged, %d from another level, %d unnamed (tree=%s)",
        level,
        sum(map(len, merged.values())),
        from_other,
        unnamed,
        data["tree"] is not None,
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


def _rename(data: dict, level: int, gap: int | None, idx: int | None, name: str) -> None:
    """Give a polygon a custom name (empty = back to its default name)."""
    name = name.strip()
    with _LAND:
        if gap is None:
            if idx is None:
                raise HTTPException(422, "Pick a polygon to rename.")
            hit = _current(data, level)
            if idx >= len(hit["props"]):
                raise HTTPException(422, "That polygon is not in this level any more.")
            p = hit["props"][idx]
            p.setdefault("orig_name", p["name"])
            p["name"] = name or p["orig_name"]
            p["renamed"] = bool(name)
        else:
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


def _fill_remaining(data: dict, level: int, source: int) -> None:
    """Fill only the gaps of the finished `level` that are still "Unnamed N" (not renamed, not merged into
    something else) with the polygons of `source`. Whatever `source` leaves empty stays "Unnamed N".
    Caller holds _LAND.
    """
    if source == level or source not in data["by_level"]:
        return
    item = _current(data, level)
    todo = [i for i, p in enumerate(item["props"]) if p.get("unnamed")]
    if not todo:
        return
    gap = _polys(shapely.union_all(item["geoms"][todo]))
    if gap is None:
        return
    lv = data["by_level"][source]
    shapely.prepare(gap)
    added: list = []
    for i in _level_tree(data, source).query(gap, predicate="intersects"):
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
        added.append(({**lv["props"][i], "admin_level": level, "from_level": source}, piece))
    if added:
        gap = _polys(_safe(shapely.difference, gap, shapely.union_all([g for _, g in added])))

    drop = set(todo)
    props = [p for i, p in enumerate(item["props"]) if i not in drop]
    geoms = [g for i, g in enumerate(item["geoms"]) if i not in drop]
    props.extend(p for p, _ in added)
    geoms.extend(g for _, g in added)
    n = max((p.get("gap") or 0 for p in item["props"]), default=0)
    if gap is not None:
        for q in shapely.get_parts(gap):
            if q.geom_type != "Polygon" or q.area < MIN_PIECE:
                continue
            n += 1
            props.append(
                {
                    "name": f"Unnamed {n}",
                    "admin_level": level,
                    "osm_id": "",
                    "iso": "",
                    "gap": n,
                    "unnamed": True,
                }
            )
            geoms.append(q)
    arr = np.empty(len(geoms), dtype=object)
    arr[:] = geoms
    data["land"][level] = {"props": props, "geoms": arr}


def _set_fill(data: dict, level: int, source: int | None) -> None:
    """Fill the gaps of `level` that are still unnamed with the polygons of another level. Renames, merges and
    deletions already made stay as they are. None = back to the original gaps ("Unnamed N"); that rebuilds the
    level, so its renames, merges and deletions are reset.
    """
    with _LAND:
        if source is None:
            data["fill"].pop(level, None)
            data["renames"].pop(level, None)
            data["land"].pop(level, None)
        else:
            data["fill"][level] = source
            _fill_remaining(data, level, source)
        _drop_preview(data, level)


MAX_UNDO = 50


def _snapshot(data: dict, level: int, item: dict) -> None:
    props = [dict(p) for p in item["props"]]
    data["undo"].append((level, {"props": props, "geoms": item["geoms"].copy()}))
    del data["undo"][:-MAX_UNDO]


def _undo(data: dict) -> int:
    with _LAND:
        if not data["undo"]:
            raise HTTPException(422, "Nothing to undo.")
        level, item = data["undo"].pop()
        data["land"][level] = item
        _drop_preview(data, level)
        return level


def _merge(data: dict, level: int, idxs: list[int]) -> dict:
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
        _snapshot(data, level, item)
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
        for k in [k for k in data["cache"] if k[0] == level]:
            if (
                len(k) == 3 and k[2] == "geoms"
            ):  # simplified polygons: join the simplified ones instead of simplifying the whole level again
                s = data["cache"][k].copy()
                try:
                    joined = shapely.union_all(s[ids])
                except Exception:  # noqa: BLE001
                    joined = shapely.union_all(shapely.make_valid(s[ids]))
                joined = _polys(joined)
                if joined is not None:
                    s[ids[0]] = joined
                data["cache"][k] = np.delete(s, list(drop))
            else:
                del data["cache"][k]
        s = data["cache"].get((level, PREVIEW_PCT, "geoms"))
        if s is None:
            return {"geometry": None, "props": None}
        props_out = {
            **keep,
            "idx": ids[0],
            "tag": f"ADM{level} · {keep['name']}",
            "color": UNNAMED_COLOR if keep.get("unnamed") else _color(level),
            "shade": 0,
        }
        return {"geometry": json.loads(shapely.to_geojson(s[ids[0]])), "props": props_out}


# ── step 3: preview / export (from memory) ─────────────────────────────────


def _color(level: int) -> str:
    return _LEVEL_COLORS[level % len(_LEVEL_COLORS)]


def _groups(parts: np.ndarray) -> np.ndarray:
    """Group number of every polygon: polygons that share a vertex (a shared border, or touching at a point) get
    the same one. Needs scipy.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    xy, owner = shapely.get_coordinates(parts, return_index=True)
    q = np.rint(xy * 1e7).astype(np.int64) + (1 << 31)  # the 1e-7 degree grid OSM itself uses
    key = (q[:, 0].astype(np.uint64) << np.uint64(32)) | q[:, 1].astype(np.uint64)
    order = np.argsort(key, kind="stable")
    k, p = key[order], owner[order]
    link = (k[1:] == k[:-1]) & (p[1:] != p[:-1])
    ones = np.ones(int(link.sum()), dtype=np.int8)
    graph = coo_matrix((ones, (p[:-1][link], p[1:][link])), shape=(len(parts), len(parts)))
    return connected_components(graph, directed=False)[1]


def _coverage_simplify(arr: np.ndarray, tol: float) -> np.ndarray:
    """shapely.coverage_simplify, but a big level is split into the groups of polygons that share borders (every
    island is a group of its own) and the groups are simplified on several threads. Borders are only ever shared
    inside a group, so the result is the one a single call gives. Small levels, a level that is one connected
    piece and a missing scipy all take the single call.
    """
    if TILE_WORKERS < 2 or int(shapely.get_num_coordinates(arr).sum()) < 2 * TILE_COORDS:
        return shapely.coverage_simplify(arr, tol)
    parts, owner = shapely.get_parts(arr, return_index=True)
    try:
        label = _groups(parts)
    except ImportError:  # pragma: no cover
        return shapely.coverage_simplify(arr, tol)
    count = int(label.max()) + 1
    if count < 2:
        return shapely.coverage_simplify(arr, tol)
    weight = np.bincount(label, weights=shapely.get_num_coordinates(parts), minlength=count)
    order = np.argsort(label, kind="stable")
    members = np.split(
        order, np.flatnonzero(np.diff(label[order])) + 1
    )  # members[g] = polygons of group g
    biggest_first = [members[g] for g in np.argsort(-weight, kind="stable").tolist()]

    def one(idx: np.ndarray) -> np.ndarray:
        try:
            return shapely.coverage_simplify(parts[idx], tol)
        except (
            Exception
        ):  # not a valid coverage (overlapping duplicates): this group stays as it is
            log.warning("coverage_simplify failed for a group, left unsimplified", exc_info=True)
            return parts[idx]

    done = np.empty(len(parts), dtype=object)
    for idx, res in zip(biggest_first, _pool_map(one, biggest_first), strict=True):
        done[idx] = res
    out = arr.copy()
    first = np.searchsorted(
        owner, np.arange(len(arr))
    )  # get_parts lists the parts geometry by geometry
    last = np.searchsorted(owner, np.arange(len(arr)), side="right")
    for i in np.unique(owner).tolist():
        mine = [g for g in done[first[i] : last[i]] if not g.is_empty]
        if mine:
            out[i] = (
                mine[0]
                if len(mine) == 1 and arr[i].geom_type == "Polygon"
                else shapely.multipolygons(mine)
            )
    return out


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
        return _coverage_simplify(arr, tol)
    except Exception:  # not a valid coverage (overlapping duplicates): better heavy than wrong
        log.warning("coverage_simplify failed, level left unsimplified", exc_info=True)
        return arr


def _tol(data: dict, pct: int) -> float:
    return 0.0 if pct <= 0 else data["diag"] * (pct / 100) * 0.01


def _simplified(data: dict, level: int, pct: int) -> np.ndarray:
    """The polygons of one level simplified together; shared by the preview and the export, computed once."""
    key = (level, pct, "geoms")
    hit = data["cache"].get(key)
    if hit is None:
        hit = data["cache"][key] = _simplify(_current(data, level)["geoms"], _tol(data, pct))
    return hit


def _overlaps(data: dict, level: int, pct: int) -> dict[int, list[int]]:
    """For every polygon of a level, the polygons of the same level it overlaps. Touching along an edge doesn't count."""
    key = (level, pct, "overlaps")
    hit = data["cache"].get(key)
    if hit is None:
        hit = {}
        geoms = _simplified(data, level, pct)
        if len(geoms) > 1:
            a, b = shapely.STRtree(geoms).query(geoms, predicate="intersects")
            keep = a < b
            a, b = a[keep], b[keep]
            inner = np.asarray(
                _safe(lambda x, y: shapely.relate_pattern(x, y, "T********"), geoms[a], geoms[b]),
                dtype=bool,
            )
            a, b = a[inner], b[inner]
            big = shapely.area(_safe(shapely.intersection, geoms[a], geoms[b])) > MIN_PIECE
            for i, j in zip(a[big].tolist(), b[big].tolist(), strict=True):
                hit.setdefault(i, []).append(j)
                hit.setdefault(j, []).append(i)
        data["cache"][key] = hit
    return hit


def _preview_features(data: dict, level: int, pct: int) -> list[str]:
    """Serialised GeoJSON features of one level at one simplification; computed once, then cached."""
    key = (level, pct)
    hit = data["cache"].get(key)
    if hit is None:
        lv = _current(data, level)
        gj = shapely.to_geojson(_simplified(data, level, pct))
        over = _overlaps(data, level, pct)
        hit = [
            '{"type":"Feature","properties":'
            + json.dumps(
                {
                    **p,
                    "idx": i,
                    "tag": f"ADM{level} · {p['name']}",
                    "color": UNNAMED_COLOR if p.get("unnamed") else _color(level),
                    "shade": 0,
                    **({"overlaps": over[i]} if i in over else {}),
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


# ── export names: strip the words that only add noise ──────────────────────

COMMON_WORD_SHARE = (
    0.5  # a word in at least this share of a level's names is dropped from all of them
)
MIN_NAMES_FOR_COMMON = 3  # fewer names than this and "common" means nothing
# Administrative-type words dropped from every name. City / town / village and similar are kept.
_ADMIN_WORDS = frozenset(
    [
        "municipality",
        "municipal",
        "county",
        "province",
        "region",
        "district",
        "partido",
        "department",
        "departamento",
        "governorate",
        "prefecture",
        "canton",
        "parish",
        "oblast",
        "voivodeship",
        "division",
        "commune",
        "territory",
    ]
)
_LINKS = frozenset(
    {"of", "de", "del", "da", "do"}
)  # left dangling at the edges once a word is gone


def _word_key(word: str) -> str:
    return word.strip(".,;:()[]\"'").casefold()


def _clean_names(names: list[str]) -> list[str]:
    """Names without the admin-type words and without the words shared by >= COMMON_WORD_SHARE of them.
    A name that would end up empty keeps its original text.
    """
    counts: Counter[str] = Counter()
    for n in names:
        counts.update({_word_key(w) for w in n.split()} - {""})
    common: set[str] = set()
    if len(names) >= MIN_NAMES_FOR_COMMON:
        common = {w for w, c in counts.items() if c >= COMMON_WORD_SHARE * len(names)}
    drop = common | _ADMIN_WORDS
    out = []
    for n in names:
        kept = [w for w in n.split() if _word_key(w) not in drop]
        while kept and _word_key(kept[0]) in _LINKS:
            kept.pop(0)
        while kept and _word_key(kept[-1]) in _LINKS:
            kept.pop()
        out.append(" ".join(kept) or n)
    return out


def _export_names(props: list[dict]) -> list[str]:
    """Export name of every polygon of one level. Filled-in gaps ("Unnamed N" or renamed) are left as they are."""
    idx = [i for i, p in enumerate(props) if p.get("gap") is None and not p.get("renamed")]
    names = [p["name"] for p in props]
    for i, clean in zip(idx, _clean_names([names[i] for i in idx]), strict=True):
        names[i] = clean
    return names


MAX_ADM = 5  # no country has more official levels than this
_NAME_PREFIX = "SUBDIVISION_NAME_"


def _layer_of(ds: dict, data: dict, spec) -> dict | None:
    """A coarser layer {"names", "geoms", "inherit"}: a level of this country as the user left it (merges, fills)
    or an imported file. None when the spec points at nothing.
    """
    if spec.file:
        imp = ds.get("imports", {}).get(spec.file)
        if imp is None:
            raise HTTPException(410, "That parent file expired. Import it again.")
        return imp
    if spec.level is not None and spec.level in data["by_level"]:
        item = _current(data, spec.level)
        return {"names": _export_names(item["props"]), "geoms": item["geoms"], "inherit": None}
    return None


def _parent_fields(geoms: np.ndarray, adm: int, layers: dict[int, dict]) -> list[dict[int, str]]:
    """For every polygon, the names of the coarser subdivisions it sits in: {ADM number: name}.

    The polygon's representative point is looked up in the directly coarser layer (adm - 1) and the coarser names
    of the polygon found there are inherited. Only what is still missing is looked up in the next coarser layers.
    """
    out: list[dict[int, str]] = [{} for _ in range(len(geoms))]
    if not len(geoms):
        return out
    pts = shapely.point_on_surface(geoms)
    for k in range(adm - 1, -1, -1):
        lay = layers.get(k)
        todo = [i for i in range(len(geoms)) if k not in out[i]]
        if lay is None or not todo or not len(lay["geoms"]):
            continue
        if lay.get("inherit") is None:
            lay["inherit"] = _parent_fields(lay["geoms"], k, layers)
        if "tree" not in lay:
            lay["tree"] = shapely.STRtree(lay["geoms"])
        first: dict[int, int] = {}
        hits = lay["tree"].query(pts[todo], predicate="intersects")
        for a, b in zip(hits[0].tolist(), hits[1].tolist(), strict=True):
            first.setdefault(a, b)
        for a, b in first.items():
            i = todo[a]
            out[i][k] = lay["names"][b]
            for j, v in lay["inherit"][b].items():
                out[i].setdefault(j, v)
    return out


def _import_parent(ds: dict, raw: bytes, filename: str) -> dict:
    """Read a polygon file (GeoJSON, TopoJSON...) to use as a coarser layer. Coarser names it already carries
    (SUBDIVISION_NAME_1...) are kept and inherited.
    """
    import pyogrio

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"parent-{uuid4().hex}{Path(filename).suffix.lower() or '.json'}"
    path.write_bytes(raw)
    try:
        df = pyogrio.read_dataframe(str(path))
    except Exception as e:
        raise HTTPException(422, f"Could not read this file: {_oneline(e)}") from e
    finally:
        path.unlink(missing_ok=True)
    keep = np.isin(shapely.get_type_id(df.geometry.to_numpy()), [3, 6]) & ~shapely.is_empty(
        df.geometry.to_numpy()
    )
    df = df[keep]
    if df.empty:
        raise HTTPException(422, "That file has no polygons.")
    geoms = shapely.make_valid(df.geometry.to_numpy())
    cols = [c for c in df.columns if c != df.geometry.name]
    coarser = {
        int(c[len(_NAME_PREFIX) :]): c
        for c in cols
        if c.startswith(_NAME_PREFIX) and c[len(_NAME_PREFIX) :] in set("012345")
    }
    own = (
        coarser.pop(max(coarser))
        if coarser
        else next((c for c in ("SUBDIVISION_NAME", "name:en", "name", "NAME") if c in cols), None)
    )
    if own is None:
        own = next((c for c in cols if df[c].dtype == object), None)
    if own is None:
        raise HTTPException(422, "No name field found in that file.")
    names = [_clean(v) for v in df[own].tolist()]
    extra = {k: [_clean(v) for v in df[c].tolist()] for k, c in coarser.items() if k >= 0}
    inherit = [{k: v[i] for k, v in extra.items() if v[i]} for i in range(len(names))]
    imports = ds.setdefault("imports", OrderedDict())
    imp_id = uuid4().hex
    imports[imp_id] = {"names": names, "geoms": geoms, "inherit": inherit}
    while len(imports) > 5:
        imports.popitem(last=False)
    return {"id": imp_id, "name": filename, "count": len(names), "field": own}


def _export(
    ds: dict, data: dict, levels: list[int], simplify: int, name: str, adm: int, parents: list
) -> tuple[Path, str]:
    chosen = [lv for lv in levels if lv in data["by_level"]]
    if not chosen:
        raise HTTPException(422, "Pick at least one admin level")
    layers: dict[int, dict] = {}
    for spec in parents:
        if 0 <= spec.adm < adm:
            lay = _layer_of(ds, data, spec)
            if lay is not None:
                layers[spec.adm] = lay
    field = f"{_NAME_PREFIX}{adm}"
    feats = []
    feat_levels = []
    shapes = []
    for lv in chosen:
        item = _current(data, lv)
        shapes.append(_simplified(data, lv, simplify))
        names = _export_names(item["props"])
        up = _parent_fields(item["geoms"], adm, layers)
        for p, label, coarser in zip(item["props"], names, up, strict=True):
            feats.append(
                {
                    "COUNTRY": data["country"],
                    "ISO": p["iso"],
                    **{f"{_NAME_PREFIX}{k}": v for k, v in sorted(coarser.items()) if k > 0},
                    **({field: label} if adm > 0 else {}),
                }
            )
            feat_levels.append(lv)

    if not feats:
        raise HTTPException(422, "Nothing of these levels is on land.")

    if len(feats) == 1 and feat_levels[0] < 6:
        feats[0].pop(field, None)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"export-{uuid4().hex}.topojson"
    topojson_writer.write_topojson(out, np.concatenate(shapes), feats, "ADM")

    stem = "".join(c if c.isascii() and (c.isalnum() or c in "._-") else "_" for c in name.strip())
    stem = stem.removesuffix(".topojson") or f"ADM{adm}"
    return out, f"{stem}.topojson"


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
    gap: int | None = Field(None, ge=1)
    idx: int | None = Field(None, ge=0)
    name: str = Field("", max_length=120)


class FillBody(CountryBody):
    level: int
    source: int | None = None


class MergeBody(CountryBody):
    level: int
    idxs: list[int] = Field(min_length=2, max_length=1000)


class DeleteBody(CountryBody):
    level: int
    idxs: list[int] = Field(min_length=1, max_length=1000)


class DiffBody(CountryBody):
    level: int
    idx: int = Field(ge=0)


class PointsBody(CountryBody):
    levels: list[int] = Field(default_factory=list)
    places: bool = False


class ParentSpec(BaseModel):
    adm: int = Field(ge=0, le=MAX_ADM)  # which coarser ADM number this source provides
    level: int | None = None  # an OSM level of the open country...
    file: str | None = None  # ...or an imported file (id from /technical/pbf/parent-file)


class ExportBody(ExtractBody):
    name: str = Field("", max_length=80)  # file name, without extension
    adm: int = Field(1, ge=0, le=MAX_ADM)  # the ADM number of what is exported
    parents: list[ParentSpec] = Field(default_factory=list, max_length=MAX_ADM)


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
    await run_in_threadpool(_rename, data, body.level, body.gap, body.idx, body.name)
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
    return await run_in_threadpool(_merge, data, body.level, body.idxs)


def _delete(data: dict, level: int, idxs: list[int]) -> None:
    """Remove polygons of a finished level (what exports and coarser-layer lookups then see)."""
    with _LAND:
        item = _current(data, level)
        drop = set(idxs)
        if min(drop) < 0 or max(drop) >= len(item["props"]):
            raise HTTPException(422, "Those polygons are not in this level any more.")
        _snapshot(data, level, item)
        keep = [i for i in range(len(item["props"])) if i not in drop]
        data["land"][level] = {
            "props": [item["props"][i] for i in keep],
            "geoms": item["geoms"][keep],
        }
        _drop_preview(data, level)


@router.post("/technical/pbf/delete")
async def delete_pbf(body: DeleteBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    if body.level not in data["by_level"]:
        raise HTTPException(422, "That admin level is not in this country.")
    await run_in_threadpool(_delete, data, body.level, body.idxs)
    return {"ok": True}


def _diff(data: dict, level: int, idx: int) -> None:
    """Cut from one polygon of a finished level everything the other polygons of that level overlap (original geometry)."""
    with _LAND:
        item = _current(data, level)
        geoms = item["geoms"]
        if idx >= len(geoms):
            raise HTTPException(422, "That polygon is not in this level any more.")
        g = geoms[idx]
        near = shapely.STRtree(geoms).query(g, predicate="intersects")
        near = near[near != idx]
        if len(near):
            hit = _safe(lambda x, y: shapely.relate_pattern(x, y, "T********"), g, geoms[near])
            near = near[np.asarray(hit, dtype=bool)]
        if not len(near):
            raise HTTPException(422, "This polygon overlaps nothing.")
        cut = _polys(_safe(shapely.difference, g, shapely.union_all(geoms[near])))
        if cut is None:
            raise HTTPException(422, "Nothing would be left of this polygon. Delete it instead.")
        _snapshot(data, level, item)
        out = geoms.copy()
        out[idx] = cut
        data["land"][level] = {"props": item["props"], "geoms": out}
        _drop_preview(data, level)


@router.post("/technical/pbf/diff")
async def diff_pbf(body: DiffBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    if body.level not in data["by_level"]:
        raise HTTPException(422, "That admin level is not in this country.")
    await run_in_threadpool(_diff, data, body.level, body.idx)
    return {"ok": True}


@router.post("/technical/pbf/points")
async def points_pbf(body: PointsBody):
    """Points of the chosen admin levels (exact: the relations' admin_centre / label nodes), plus the other
    place=* nodes when `places` is set (their level is unknown).
    """
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    levels = set(_levels(body.levels))
    pts = [p for p in data["points"]["relation"] if p["admin_level"] in levels]
    if body.places:
        pts += data["points"]["place"]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {k: v for k, v in p.items() if k not in {"lon", "lat"}},
                "geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]},
            }
            for p in pts
        ],
    }


@router.post("/technical/pbf/undo")
async def undo_pbf(body: CountryBody):
    data = await run_in_threadpool(_load, _dataset(body.token), body.country)
    level = await run_in_threadpool(_undo, data)
    return {"level": level}


@router.post("/technical/pbf/parent-file")
async def parent_file_pbf(request: Request, token: str, filename: str = "parent.json"):
    """A polygon file (GeoJSON / TopoJSON) to use as a coarser layer when exporting."""
    ds = _dataset(token)
    raw = await request.body()
    if not raw:
        raise HTTPException(422, "Empty file")
    return await run_in_threadpool(_import_parent, ds, raw, filename)


@router.post("/technical/pbf/export")
async def export_pbf(body: ExportBody):
    ds = _dataset(body.token)
    data = await run_in_threadpool(_load, ds, body.country)
    path, filename = await run_in_threadpool(
        _export, ds, data, _levels(body.levels), body.simplify, body.name, body.adm, body.parents
    )
    return FileResponse(
        path,
        media_type="application/topojson+json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        background=BackgroundTask(path.unlink, missing_ok=True),
    )
