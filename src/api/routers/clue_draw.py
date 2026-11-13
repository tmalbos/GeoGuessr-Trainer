"""clue_draw.py — Backing for the "Draw" region editor of a regional clue.

GET  /clues/{cc}/draw/units?level=N  -> {levels, units: [{id, name, geometry}]}. Level 0 is the whole country;
                                        1..levels are the local admin layer's levels (state, subregion, city).
POST /clues/{cc}/draw/build          -> GeoJSON FeatureCollection, one colored feature per category name, built from
                                        the drawing's items in order: fills of a subdivision and freeform strokes
                                        (clipped to the country outline). A later item wins over an earlier one.
"""

import colorsys
import json
import math
from typing import Literal

import geopandas as gpd
import numpy as np
import shapely
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.api.routers.clues import _cc  # noqa: PLC2701
from src.api.routers.stain import _outline  # noqa: PLC2701
from src.geo.csv_areas import LEVEL_COLUMNS, _lab  # noqa: PLC2701
from src.geo.simplify import _polygonal, simplify_coverage, tolerance  # noqa: PLC2701

router = APIRouter()

# Hand-picked, the app's coral / sea / sand / green accents softened to sit on the dark UI. After these, each new color
# is the candidate farthest (Lab distance) from all used ones.
PALETTE = [
    "#d9775f",
    "#5d9fc4",
    "#d6b65e",
    "#5fae88",
    "#9d90cf",
    "#cf7ca2",
    "#69bfb8",
    "#c4b78c",
    "#9bba5f",
]
PALE = "#f6f4e4"  # the land color of the clue maps: used when the areas are not painted with different colors
ADJ_TOL_DEG = 0.005  # areas closer than this (~500 m) count as neighbours
MIN_DISTANCE = 40.0  # Lab distance a color should keep from its neighbours' colors


def _rgb(h: str) -> list[float]:
    return [int(h[i : i + 2], 16) / 255 for i in (1, 3, 5)]


def _spread_colors(n: int) -> list[str]:
    cols = list(PALETTE)
    labs = [_lab(_rgb(c)) for c in cols]
    pool = []
    for hue in range(0, 360, 15):
        for light in (0.5, 0.6):
            for sat in (0.3, 0.45):
                rgb = colorsys.hls_to_rgb(hue / 360, light, sat)
                pool.append(("#" + "".join(f"{round(v * 255):02x}" for v in rgb), _lab(rgb)))
    while len(cols) < n:
        hex_, lab = max(pool, key=lambda p: min(float(np.linalg.norm(p[1] - q)) for q in labs))
        cols.append(hex_)
        labs.append(lab)
    return cols


COLORS = _spread_colors(40)
_COLORS_LAB = np.array([_lab(_rgb(c)) for c in COLORS])


def _distribute(geoms: list) -> list[str]:
    """One color per geometry, chosen so touching areas get colors as far apart (Lab) as possible.

    Greedy (most neighbours first), then a few passes that re-pick each area's color against its final neighbours.
    Among equally good colors the least used one wins, so the colors spread out evenly.
    """
    n = len(geoms)
    if not n:
        return []
    nbrs: list[set[int]] = [set() for _ in range(n)]
    if n > 1:
        arr = np.empty(n, dtype=object)
        arr[:] = geoms
        a, b = shapely.STRtree(arr).query(arr, predicate="dwithin", distance=ADJ_TOL_DEG)
        for i, j in zip(a.tolist(), b.tolist(), strict=True):
            if i != j:
                nbrs[i].add(j)
                nbrs[j].add(i)
    chosen = [-1] * n
    used = np.zeros(len(COLORS))
    order = sorted(range(n), key=lambda k: (-len(nbrs[k]), k))
    for _ in range(4):
        for i in order:
            if chosen[i] >= 0:
                used[chosen[i]] -= 1
            near = [chosen[j] for j in nbrs[i] if chosen[j] >= 0]
            if near:
                dist = np.linalg.norm(
                    _COLORS_LAB[:, None, :] - _COLORS_LAB[near][None], axis=2
                ).min(1)
                score = np.minimum(dist, MIN_DISTANCE)
            else:
                score = np.zeros(len(COLORS))
            ok = np.flatnonzero(score >= score.max() - 1e-9)
            chosen[i] = int(ok[np.argmin(used[ok])])
            used[chosen[i]] += 1
    return [COLORS[c] for c in chosen]


def restandardize(path) -> None:
    """Recompute the colors of a drawn-areas file (name + color columns) so touching areas never look alike.

    A file whose areas are all the pale land color (painted without colors) is left alone, and so is a file
    whose colors are already right.
    """
    if not path.is_file():
        return
    gdf = gpd.read_parquet(path)
    if not {"name", "color"} <= set(gdf.columns):
        return
    if set(gdf["color"].astype(str).str.lower()) <= {PALE}:
        return
    keys = gdf["name"].astype(str)
    names = list(dict.fromkeys(keys))
    geoms = [shapely.union_all(gdf.geometry[keys == n].to_numpy()) for n in names]
    colors = keys.map(dict(zip(names, _distribute(geoms), strict=True)))
    if (colors == gdf["color"].astype(str)).all():
        return
    gdf["color"] = colors
    gdf.to_parquet(path, compression="zstd")


_UNITS: dict[tuple[str, int], list[tuple[str, object]]] = {}


def _local(request: Request):
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    return local


def _layer(local, cc: str):
    layer = local._admin_layer(cc)  # noqa: SLF001
    cols = [c for c in LEVEL_COLUMNS if layer is not None and c in layer.columns]
    return layer, cols


def _units(local, cc: str, level: int) -> list[tuple[str, object]]:
    """[(name, geometry)] of the country's units at a level, simplified together. Cached."""
    key = (cc, level)
    if key in _UNITS:
        return _UNITS[key]
    if level == 0:
        outline = _outline(local, cc)
        out = [] if outline is None else [(cc, outline)]
    else:
        layer, all_cols = _layer(local, cc)
        cols = all_cols[:level]
        if level < 0 or len(cols) < level:
            raise HTTPException(422, "That level isn't available for this country.")
        keys = layer[cols].fillna("").astype(str).apply(lambda s: s.str.strip())
        labels = keys[cols[0]]
        for c in cols[1:]:
            labels = labels + " · " + keys[c]
        names, geoms = [], []
        for label, grp in layer.groupby(labels, sort=True):
            g = _polygonal(shapely.union_all(shapely.make_valid(grp.geometry.to_numpy())))
            if g is not None:
                names.append(str(label).strip(" ·") or "—")
                geoms.append(g)
        out = []
        if geoms:
            bounds = shapely.total_bounds(np.array(geoms, dtype=object))
            out = list(zip(names, simplify_coverage(geoms, tolerance(bounds)), strict=True))
    _UNITS[key] = out
    return out


def _payload(local, cc: str, level: int) -> dict:
    units = _units(local, cc, level)
    return {
        "levels": len(_layer(local, cc)[1]),
        "colors": COLORS,
        "units": [
            {"id": i, "name": n, "geometry": json.loads(shapely.to_geojson(g))}
            for i, (n, g) in enumerate(units)
        ],
    }


@router.get("/clues/{cc}/draw/units")
async def draw_units(cc: str, request: Request, level: int = 0):
    return await run_in_threadpool(_payload, _local(request), _cc(cc), level)


class Item(BaseModel):
    op: Literal["fill", "stroke", "erase", "shape"]
    name: str = ""
    slot: int = 0  # index of the category's color in COLORS (wraps around)
    level: int = 0
    id: int = 0
    width: float = 0.0  # stroke width, degrees of latitude
    points: list[list[float]] = []  # stroke path, lon/lat
    extra: str = ""  # image (data URI) of the category; sent on its first item
    geometry: dict | None = (
        None  # shape: GeoJSON of an area of a saved drawing that is being edited
    )


class BuildBody(BaseModel):
    items: list[Item]
    colored: bool = True  # False = every area keeps the pale land color


def _stroke(points: list[list[float]], width: float):
    k = math.cos(math.radians(points[0][1])) or 1e-6
    pts = [(p[0] * k, p[1]) for p in points]
    line = shapely.Point(pts[0]) if len(pts) == 1 else shapely.LineString(pts)
    g = line.buffer(max(width, 1e-6) / 2, quad_segs=4)
    return shapely.transform(g, lambda c: c / np.array([k, 1.0]))


def _build(local, cc: str, items: list[Item], colored: bool = True) -> dict:
    outline = _outline(local, cc)
    if outline is None or outline.is_empty:
        raise HTTPException(422, f"No local outline for {cc}.")
    shapes: dict[str, object] = {}
    extras: dict[str, str] = {}
    slots: dict[str, int] = {}
    for it in items:
        if it.op == "shape":
            if not it.geometry:
                continue
            shape = shapely.make_valid(shapely.from_geojson(json.dumps(it.geometry)))
            geom = shapely.intersection(shape, outline)
        elif it.op == "fill" or (it.op == "erase" and not it.points):
            units = _units(local, cc, it.level)
            if not 0 <= it.id < len(units):
                continue
            geom = shapely.intersection(units[it.id][1], outline)
        else:
            pts = [p for p in it.points if len(p) >= 2]
            if not pts:
                continue
            geom = shapely.intersection(_stroke(pts, it.width), outline)
        if geom.is_empty:
            continue
        if it.op == "erase":
            for k in shapes:
                shapes[k] = shapely.difference(shapes[k], geom)
            continue
        name = it.name.strip()
        if not name:
            continue
        slots.setdefault(name, it.slot)
        if it.extra:
            extras[name] = it.extra
        for k in shapes:
            if k != name:
                shapes[k] = shapely.difference(shapes[k], geom)
        shapes[name] = shapely.union(shapes[name], geom) if name in shapes else geom
    names, geoms = [], []
    for n, g in shapes.items():
        g = _polygonal(g)
        if g is not None:
            names.append(n)
            geoms.append(g)
    colors = _distribute(geoms) if colored else [PALE] * len(geoms)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"name": n, "color": c, "extra": extras.get(n, "")},
                "geometry": json.loads(shapely.to_geojson(g)),
            }
            for n, c, g in zip(names, colors, geoms, strict=True)
        ],
    }


@router.post("/clues/{cc}/draw/build")
async def draw_build(cc: str, body: BuildBody, request: Request):
    return await run_in_threadpool(_build, _local(request), _cc(cc), body.items, body.colored)
