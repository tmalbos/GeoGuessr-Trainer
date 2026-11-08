"""technical.py — Technical tools. First one: polygonize tagged points from a mapmaking.app export.

Flow:  POST /technical/mapmaking   -> raw JSON file in the body; parsed and kept in memory, returns tags + points
       POST /technical/mapmaking/country/{code} -> same, but only the locations inside that country are kept
                                      (used by the clue editor)
       POST /technical/polygonize  -> {token, tags, link_km, pad_km, curve} -> one GeoJSON feature per selected tag
       POST /technical/polygonize/base -> {.., code?} -> countries spanned, or that country + the shapes cut to it (image export)
                                      With `code` it also returns `spread_km` (see _spread_km), used by the clue editor
                                      to rate the frequency of a mapmaking region.
Datasets live in memory only (last few uploads); a server restart means loading the file again (410).
Distances are approximated in degrees (1 deg ~ 111 km), fine for a first pass but loose near the poles.
"""

import colorsys
import json
import math
import threading
from collections import OrderedDict
from pathlib import Path
from uuid import uuid4

import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

try:  # point density for the "rarer areas" shades; without scipy the shades are skipped
    from scipy.spatial import cKDTree
except ImportError:  # pragma: no cover
    cKDTree = None

from src.api.deps import get_ctx

router = APIRouter()

KM_PER_DEG = 111.32
MAX_DATASETS = 3
MAX_CACHE = 300
COUNTRY_SLACK_DEG = (
    0.01  # ~1 km: a location on the coast still counts as inside the (simplified) outline
)

_DATASETS: OrderedDict[str, dict] = OrderedDict()
_LOCK = threading.Lock()
_COMPUTE = threading.Lock()  # one polygon computation at a time (they are CPU-bound)
_VALID: dict[
    int, tuple
] = {}  # id(geometry) -> (geometry, repaired copy); only touched inside _COMPUTE
_WIDE: dict[
    str, object
] = {}  # country code -> outline widened by COUNTRY_SLACK_DEG (for "is this point inside")


# ── parsing ────────────────────────────────────────────────────────────────


class _NotMapmaking(HTTPException):
    """The file is valid input for something else (e.g. a GeoJSON), just not a mapmaking.app export."""

    def __init__(self, msg: str) -> None:
        super().__init__(422, msg)


def _hex(rgb) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(max(0, min(255, int(v))) for v in rgb[:3]))


def _fallback_color(i: int) -> str:
    r, g, b = colorsys.hsv_to_rgb((i * 0.618034) % 1, 0.65, 0.95)
    return _hex((r * 255, g * 255, b * 255))


def _parse(raw: bytes, inside=None) -> dict:
    """`inside(lats, lngs) -> bool array` (optional): locations it rejects are dropped, and so are the
    tags that end up with no location left.
    """
    try:
        data = json.loads(raw)
    except ValueError as e:
        msg = "That file isn't valid JSON"
        raise _NotMapmaking(msg) from e
    coords = data.get("customCoordinates") if isinstance(data, dict) else None
    if not isinstance(coords, list):
        msg = "Not a mapmaking.app export: customCoordinates is missing"
        raise _NotMapmaking(msg)
    meta = ((data.get("extra") or {}).get("tags")) or {}

    items: list[tuple] = []
    for c in coords:
        if not isinstance(c, dict):
            continue
        lat, lng = c.get("lat"), c.get("lng")
        if not isinstance(lat, int | float) or not isinstance(lng, int | float):
            continue
        tags = [t for t in (c.get("extra") or {}).get("tags") or [] if isinstance(t, str)]
        if tags:
            items.append((lat, lng, tags))

    outside = 0
    if inside is not None and items:
        keep = np.asarray(
            inside(
                np.array([i[0] for i in items], dtype=float),
                np.array([i[1] for i in items], dtype=float),
            ),
            dtype=bool,
        )
        outside = int(len(items) - keep.sum())
        items = [it for it, k in zip(items, keep, strict=True) if k]
    if not items:
        raise HTTPException(
            422,
            "None of the tagged locations are inside this country"
            if inside is not None
            else "No tagged locations found in this file",
        )

    index: dict[str, int] = {}
    points: list[list] = []
    for lat, lng, tags in items:
        ids = [index.setdefault(t, len(index)) for t in tags]
        points.append([round(lat, 5), round(lng, 5), ids])

    names = list(index)
    xy_lists: list[list] = [[] for _ in names]
    for lat, lng, ids in points:
        for i in ids:
            xy_lists[i].append((lng, lat))
    xy = {n: np.unique(np.array(xy_lists[i], dtype=float), axis=0) for i, n in enumerate(names)}

    tags = []
    for i, n in enumerate(names):
        col = (meta.get(n) or {}).get("color")
        tags.append(
            {
                "name": n,
                "count": len(xy_lists[i]),
                "color": _hex(col)
                if isinstance(col, list) and len(col) >= 3
                else _fallback_color(i),
            }
        )
    order = sorted(range(len(tags)), key=lambda i: -tags[i]["count"])
    remap = {old: new for new, old in enumerate(order)}
    tags = [tags[i] for i in order]
    points = [[la, ln, [remap[i] for i in ids]] for la, ln, ids in points]
    return {
        "name": str(data.get("name") or "Map"),
        "total": len(coords),
        "outside": outside,
        "tags": tags,
        "points": points,
        "xy": xy,
        "cache": {},
        "gen": 0,
    }


@router.post("/technical/mapmaking")
async def load_mapmaking(request: Request):
    """Raw file bytes in the body (same style as the clue uploads)."""
    raw = await request.body()
    if not raw:
        raise HTTPException(422, "Empty file")
    ds = await run_in_threadpool(_parse, raw)
    token = uuid4().hex
    with _LOCK:
        _DATASETS[token] = ds
        while len(_DATASETS) > MAX_DATASETS:
            _DATASETS.popitem(last=False)
    return {k: ds[k] for k in ("name", "total", "tags", "points")} | {"token": token}


def _parse_in_country(raw: bytes, local, code: str) -> dict:
    def inside(lat, lng):  # only runs once the file is known to be a mapmaking export
        if local is None:
            raise HTTPException(503, "Geo layers are not loaded yet. Check the Settings page.")
        geom = _country_geom(local, code)
        if geom is None:
            raise HTTPException(422, f"No local outline for {code}")
        wide = _WIDE.get(code)
        if wide is None:
            wide = _WIDE[code] = shapely.buffer(shapely.simplify(geom, 0.002), COUNTRY_SLACK_DEG)
        return shapely.contains_xy(wide, lng, lat)

    with _COMPUTE:  # _country_geom / _valid are only touched inside the lock
        return _parse(raw, inside)


@router.post("/technical/mapmaking/country/{code}")
async def load_mapmaking_for_country(code: str, request: Request, path: str | None = None):
    """Like /technical/mapmaking, but only the locations inside country `code` are kept, and only the tags that
    still have locations are listed. `total` = locations in the file, `outside` = the ones that were dropped.
    Raw bytes in the body, or `?path=` for a file on this machine. A file that is not a mapmaking export
    answers {"mapmaking": false} so the caller can treat it as a normal geometry file.
    """
    if len(code) != 2 or not code.isalpha():
        raise HTTPException(422, "Invalid country code")
    if path is not None:
        p = Path(path.strip().strip('"'))
        if not p.is_file():
            raise HTTPException(404, f"No file at {p}")
        raw = await run_in_threadpool(p.read_bytes)
    else:
        raw = await request.body()
    if not raw:
        raise HTTPException(422, "Empty file")
    try:
        ds = await run_in_threadpool(
            _parse_in_country, raw, get_ctx(request).local_geo, code.upper()
        )
    except _NotMapmaking:
        return {"mapmaking": False}
    token = uuid4().hex
    with _LOCK:
        _DATASETS[token] = ds
        while len(_DATASETS) > MAX_DATASETS:
            _DATASETS.popitem(last=False)
    return {k: ds[k] for k in ("name", "total", "outside", "tags")} | {
        "token": token,
        "mapmaking": True,
    }


# ── polygonizing ───────────────────────────────────────────────────────────

QUAD = 16  # segments per quarter circle: low values make the outlines look blocky
CLIP_MARGIN = 0.5  # degrees of land kept around the shape when clipping
HIT_SHARE = (
    0.05  # a country counts for an image export when it holds at least this share of the shape
)
SPREAD_SAMPLE = 400  # a cluster with more points than this is measured on an evenly spaced subset


class PolyBody(BaseModel):
    token: str
    tags: list[str]
    link_km: float = Field(100, ge=1, le=3000)  # points closer than this belong to the same area
    pad_km: float = Field(10, ge=0, le=500)  # minimum radius painted around every point
    curve: float = Field(0.3, ge=0, le=1)  # 0 = as drawn, 1 = very rounded outlines
    shades: int = Field(
        0, ge=0, le=4
    )  # lighter tones for the sparser parts of a tag (0 = one flat shape)
    density_km: float = Field(
        100, ge=1, le=3000
    )  # "rare" = few other points within this distance (shades only)
    hole_km2: float = Field(
        10000, ge=0, le=1_000_000
    )  # holes inside a shape smaller than this are filled (0 = keep all)


def _thin(xy: np.ndarray, p: PolyBody) -> np.ndarray:
    """Keep one point per small grid cell. Points that close add nothing to the shape
    (the cell is a fraction of the radius painted around each point) but cost a lot to union.
    """
    cell = max(p.pad_km, p.link_km / 10) / 4 / KM_PER_DEG
    key = np.floor(xy / cell).astype(np.int64)
    _, idx = np.unique(key, axis=0, return_index=True)
    return xy[np.sort(idx)]


def _smooth_ring(coords, sigma: float):
    """Low-pass a closed ring along its length. Rings too small for sigma are left alone."""
    p = np.asarray(coords, float)
    q = p[:-1]
    if len(q) < 5:
        return p
    seg = np.hypot(*(np.roll(q, -1, axis=0) - q).T)
    per = float(seg.sum())
    if per < 8 * sigma:
        return p
    m = int(min(4000, max(16, per / (sigma / 3))))
    space = per / m
    s = np.arange(m) * space
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    qc = np.vstack([q, q[:1]])
    x = np.interp(s, cum, qc[:, 0])
    y = np.interp(s, cum, qc[:, 1])
    k = max(1, int(np.ceil(3 * sigma / space)))
    t = np.arange(-k, k + 1)
    w = np.exp(-0.5 * (t * space / sigma) ** 2)
    w /= w.sum()
    xs = np.convolve(np.pad(x, k, mode="wrap"), w, mode="valid")
    ys = np.convolve(np.pad(y, k, mode="wrap"), w, mode="valid")
    out = np.column_stack([xs, ys])
    return np.vstack([out, out[:1]])


def _smooth(g, sigma: float):
    """Round the outlines of a (multi)polygon. A part that would become invalid keeps its outline."""
    if g is None or g.is_empty or sigma <= 0:
        return g
    parts = []
    for pg in shapely.get_parts(g):
        if pg.geom_type != "Polygon":
            continue
        sp = shapely.Polygon(
            _smooth_ring(pg.exterior.coords, sigma),
            [_smooth_ring(r.coords, sigma) for r in pg.interiors],
        )
        parts.append(sp if sp.is_valid and not sp.is_empty else pg)
    return shapely.union_all(parts) if parts else g


def _fill_holes(g, max_km2: float):
    """Fill the interior holes of a (multi)polygon that are smaller than max_km2. The outer edge is untouched."""
    if g is None or g.is_empty or max_km2 <= 0:
        return g
    parts = []
    for pg in shapely.get_parts(g):
        if pg.geom_type != "Polygon":
            continue
        keep = []
        for ring in pg.interiors:
            hole = shapely.Polygon(ring)
            km2 = hole.area * KM_PER_DEG**2 * math.cos(math.radians(hole.centroid.y))
            if km2 > max_km2:
                keep.append(ring)
        parts.append(shapely.Polygon(pg.exterior, keep))
    return shapely.union_all(parts) if parts else g


def _shape(xy: np.ndarray, p: PolyBody):
    link = p.link_km / KM_PER_DEG
    pad = max(p.pad_km / KM_PER_DEG, 1e-4)
    pts = shapely.points(_thin(xy, p))
    # Grow by pad + link/2, shrink by link/2: gaps up to ~link get closed, nothing else moves.
    g = shapely.union_all(shapely.buffer(pts, pad + link / 2, quad_segs=QUAD))
    g = shapely.buffer(g, -link / 2, quad_segs=QUAD)
    g = _fill_holes(g, p.hole_km2)
    return _smooth(g, p.curve * 0.25 * link)


def _cumulative_sets(xy: np.ndarray, p: PolyBody) -> list[np.ndarray]:
    """Point sets for the shades: [densest points, + the next density tier, ..., all points].

    Density = how many points lie within `density_km` of a point. With shades == 0 (or without scipy) there is
    a single set with every point. Tiers that come out empty are skipped, so there can be fewer than shades + 1.
    """
    n = p.shades
    if n == 0 or cKDTree is None or len(xy) < 2:
        return [xy]
    k = math.cos(math.radians(float(xy[:, 1].mean()))) or 1e-6
    flat = np.column_stack([xy[:, 0] * k, xy[:, 1]])
    dens = cKDTree(flat).query_ball_point(flat, p.density_km / KM_PER_DEG, return_length=True)
    thr = np.quantile(dens, np.arange(1, n + 1) / (n + 1))
    level = n - np.searchsorted(thr, dens, side="right")  # 0 = densest
    sets: list[np.ndarray] = []
    for j in range(n + 1):
        sub = xy[level <= j]
        if len(sub) and (not sets or len(sub) > len(sets[-1])):
            sets.append(sub)
    return sets


def _minus(g, other):
    try:
        return shapely.difference(g, other)
    except Exception:  # noqa: BLE001
        return shapely.difference(shapely.make_valid(g), shapely.make_valid(other))


def _polys(g):
    """Only the polygonal part of a geometry, as one Polygon / MultiPolygon (or None)."""
    if g is None:
        return None
    parts = [q for q in shapely.get_parts(g) if q.geom_type == "Polygon" and not q.is_empty]
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else shapely.multipolygons(parts)


def _valid(geom):
    """Repaired copy of a country geometry, cached (repairing a whole country is slow)."""
    if len(_VALID) > 300:
        _VALID.clear()
    hit = _VALID.get(id(geom))
    if hit is None or hit[0] is not geom:
        hit = _VALID[id(geom)] = (geom, shapely.make_valid(geom))
    return hit[1]


def _intersect(g, other):
    try:
        return shapely.intersection(g, other)
    except Exception:  # noqa: BLE001
        return shapely.intersection(shapely.make_valid(g), other)


def _land_near(local, bounds):
    """Land of the locally known countries around a box, or None. Only the countries that touch the
    box are used, each cut down to it: no world-sized union (that is what used to eat the memory).
    """
    w, s, e, n = bounds
    w, s, e, n = w - CLIP_MARGIN, s - CLIP_MARGIN, e + CLIP_MARGIN, n + CLIP_MARGIN
    parts = [
        shapely.clip_by_rect(_valid(geom), w, s, e, n)
        for _, geom in local.countries_in_bbox(w, s, e, n)
    ]
    parts = [q for q in parts if not q.is_empty]
    return shapely.union_all(parts) if parts else None


def _run(ds: dict, p: PolyBody, local, gen: int | None) -> tuple[list[dict], list[str]]:
    """Shapes are cut to the locally known countries (local=None: not possible, left as drawn). gen=None: never cancelled."""
    colors = {t["name"]: t["color"] for t in ds["tags"]}
    tol = min(0.02, max(0.0002, p.link_km / KM_PER_DEG * 0.005))
    feats, warns = [], []
    with _COMPUTE:
        for tag in p.tags:
            if (
                gen is not None and ds["gen"] != gen
            ):  # replaced by a newer request: the browser dropped it
                return [], []
            key = (
                tag,
                p.link_km,
                p.pad_km,
                round(p.curve, 2),
                p.shades,
                p.density_km,
                p.hole_km2,
                local is not None,
            )
            hit = ds["cache"].get(key)
            if hit is None:
                tiers: list = []  # shade 0 = the densest part, higher = rarer; the parts don't overlap
                unclipped = False
                prev = None
                for sub in _cumulative_sets(ds["xy"][tag], p):
                    g = _shape(sub, p)
                    if g is not None and not g.is_empty and local is not None:
                        land = _land_near(local, g.bounds)
                        if land is None:
                            unclipped = True
                        else:
                            g = _intersect(g, land)
                    if g is not None:
                        g = _polys(shapely.simplify(g, tol, preserve_topology=True))
                    part = g if prev is None or g is None else _polys(_minus(g, prev))
                    tiers.append(None if part is None else json.loads(shapely.to_geojson(part)))
                    if g is not None:
                        prev = g
                hit = (tiers, len(ds["xy"][tag]), unclipped)
                while len(ds["cache"]) >= MAX_CACHE:
                    ds["cache"].pop(next(iter(ds["cache"])))
                ds["cache"][key] = hit
            tiers, n_pts, unclipped = hit
            if unclipped:
                warns.append(tag)
            for shade, geom in enumerate(tiers):
                if geom:
                    feats.append(
                        {
                            "type": "Feature",
                            "properties": {
                                "tag": tag,
                                "color": colors[tag],
                                "points": n_pts,
                                "shade": shade,
                            },
                            "geometry": geom,
                        }
                    )
    return feats, warns


@router.post("/technical/polygonize")
async def polygonize(body: PolyBody, request: Request):
    ds = _DATASETS.get(body.token)
    if ds is None:
        raise HTTPException(410, "That upload expired. Add the file again.")
    missing = [t for t in body.tags if t not in ds["xy"]]
    if missing:
        raise HTTPException(422, f"Unknown tags: {', '.join(missing[:3])}")
    local = get_ctx(request).local_geo
    warning = "" if local is not None else "Geo layers aren't loaded yet, so nothing was clipped."
    ds["gen"] += 1
    feats, unclipped = await run_in_threadpool(_run, ds, body, local, ds["gen"])
    if unclipped:
        warning = f"No local country outline covers: {', '.join(unclipped[:3])}. Not clipped."
    return {"geojson": {"type": "FeatureCollection", "features": feats}, "warning": warning}


# ── image export: the shapes on top of their country ───────────────────────


class BaseBody(PolyBody):
    code: str | None = None  # None = just say which countries the shapes touch


def _country_geom(local, code: str):
    """The country outline at the chosen resolution (ready-made by scripts/convert_geo.py), or None."""
    return local.outline(code)


def _borders(local, code: str):
    """First-level division borders of the country as lines, or None when there is no local admin data."""
    return local.borders(code)


def _countries_hit(local, g) -> list[dict]:
    """Countries that hold a real share of the shape (not just a sliver spilling over a border)."""
    c = local.countries
    if g is None or g.is_empty or c is None or c.empty:
        return []
    names = dict(zip(c["code"], c["name"], strict=True))
    by: dict[str, list] = {}
    for code, geom in local.countries_in_bbox(*g.bounds):
        by.setdefault(code, []).append(geom)
    area = g.area or 1e-12
    out = []
    for code, geoms in by.items():
        land = shapely.union_all([shapely.clip_by_rect(_valid(x), *g.bounds) for x in geoms])
        share = _intersect(g, land).area / area
        if share >= HIT_SHARE:
            out.append(
                {"code": code, "name": str(names.get(code) or code), "share": round(share, 3)}
            )
    return sorted(out, key=lambda x: -x["share"])


def _mean_pairwise_km(xy: np.ndarray) -> float:
    """Mean distance between the points of one cluster, in km. One point = 0."""
    if len(xy) < 2:
        return 0.0
    if len(xy) > SPREAD_SAMPLE:
        xy = xy[:: -(-len(xy) // SPREAD_SAMPLE)]
    k = math.cos(math.radians(float(xy[:, 1].mean())))
    p = np.column_stack([xy[:, 0] * k, xy[:, 1]]) * KM_PER_DEG
    d = np.hypot(p[:, None, 0] - p[None, :, 0], p[:, None, 1] - p[None, :, 1])
    n = len(p)
    return float(d.sum() / (n * (n - 1)))


def _spread_km(polys: list, xy: np.ndarray) -> float | None:
    """Average, over the polygons, of the mean distance between the points inside each polygon (km).

    A cluster is one individual polygon of the generated shape. Every point goes to the polygon(s)
    that contain it; points left outside every polygon (cut away by the country) are ignored.
    """
    if not polys or not len(xy):
        return None
    hits = shapely.STRtree(polys).query(shapely.points(xy), predicate="intersects")
    spreads = []
    for j in range(len(polys)):
        mine = xy[hits[0][hits[1] == j]]
        if len(mine):
            spreads.append(_mean_pairwise_km(mine))
    return round(float(np.mean(spreads)), 3) if spreads else None


def _base(ds: dict, body: BaseBody, local) -> dict:
    feats, _ = _run(ds, body, local, None)
    if not feats:
        raise HTTPException(422, "There is nothing to export yet.")
    items = [(f, shapely.from_geojson(json.dumps(f["geometry"]))) for f in feats]
    # The spread ignores the shades: it is measured on the flat shape (what shades = 0 would give).
    flat = None
    if body.code is not None:
        flat = _run(ds, body.model_copy(update={"shades": 0}), local, None)[0]
    with _COMPUTE:
        if body.code is None:
            return {"countries": _countries_hit(local, shapely.union_all([g for _, g in items]))}
        code = body.code.upper()
        full = _country_geom(local, code)
        if full is None:
            raise HTTPException(422, f"No local outline for {code}")
        out = []
        for f, g in items:
            part = _polys(_intersect(g, shapely.clip_by_rect(full, *g.bounds)))
            if part is not None:
                out.append({**f, "geometry": json.loads(shapely.to_geojson(part))})
        clusters = []
        for f in flat or []:
            g = shapely.from_geojson(json.dumps(f["geometry"]))
            part = _polys(_intersect(g, shapely.clip_by_rect(full, *g.bounds)))
            if part is not None:
                clusters.extend(q for q in shapely.get_parts(part) if q.geom_type == "Polygon")
        xy = np.unique(np.vstack([ds["xy"][t] for t in body.tags]), axis=0)
        borders = _borders(local, code)
        return {
            "outline": json.loads(shapely.to_geojson(shapely.simplify(full, 0.005))),
            "borders": None if borders is None else json.loads(shapely.to_geojson(borders)),
            "features": {"type": "FeatureCollection", "features": out},
            "spread_km": _spread_km(clusters, xy),
        }


@router.post("/technical/polygonize/base")
async def polygonize_base(body: BaseBody, request: Request):
    """Without `code`: the countries the shapes span. With `code`: that country's outline plus the shapes cut to it."""
    ds = _DATASETS.get(body.token)
    if ds is None:
        raise HTTPException(410, "That upload expired. Add the file again.")
    missing = [t for t in body.tags if t not in ds["xy"]]
    if missing:
        raise HTTPException(422, f"Unknown tags: {', '.join(missing[:3])}")
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Settings page.")
    return await run_in_threadpool(_base, ds, body, local)
