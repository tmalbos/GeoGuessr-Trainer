"""canvas.py — The painted state of the "Draw" region editor: units, colors and the canvas replay."""

import colorsys
import json
import math
import threading
from collections import OrderedDict
from typing import Literal

import geopandas as gpd
import numpy as np
import shapely
from fastapi import HTTPException
from pydantic import BaseModel

from src.explore.country_clues.edit_mode.uploads.csvs import _lab, level_columns
from src.explore.country_clues.regional_stain_areas import _outline
from src.geo.simplify import _polygonal

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
SIMILAR = (
    25.0  # on save, touching areas whose colors are closer than this (Lab distance) get recolored
)


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


def _neighbours(geoms: list) -> list[set[int]]:
    """For each geometry, the indexes of the ones that touch it (or are within ADJ_TOL_DEG)."""
    n = len(geoms)
    nbrs: list[set[int]] = [set() for _ in range(n)]
    if n > 1:
        arr = np.empty(n, dtype=object)
        arr[:] = geoms
        a, b = shapely.STRtree(arr).query(arr, predicate="dwithin", distance=ADJ_TOL_DEG)
        for i, j in zip(a.tolist(), b.tolist(), strict=True):
            if i != j:
                nbrs[i].add(j)
                nbrs[j].add(i)
    return nbrs


def _distribute(geoms: list) -> list[str]:
    """One color per geometry, chosen so touching areas get colors as far apart (Lab) as possible.

    Greedy (most neighbours first), then a few passes that re-pick each area's color against its final neighbours.
    Among equally good colors the least used one wins, so the colors spread out evenly.
    """
    n = len(geoms)
    if not n:
        return []
    nbrs = _neighbours(geoms)
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


def _clash(geoms: list, colors: list[str]) -> bool:
    """True when two touching areas have colors closer than SIMILAR (Lab distance)."""
    labs = [_lab(_rgb(c)) for c in colors]
    near = _neighbours(geoms)
    return any(
        float(np.linalg.norm(labs[i] - labs[j])) < SIMILAR
        for i in range(len(geoms))
        for j in near[i]
    )


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
    have = [gdf["color"].astype(str)[keys == n].iloc[0] for n in names]
    if not _clash(
        geoms, have
    ):  # touching areas already look different: keep the colors as they are
        return
    colors = keys.map(dict(zip(names, _distribute(geoms), strict=True)))
    gdf["color"] = colors
    gdf.to_parquet(path, compression="zstd")


_UNITS: dict[tuple[str, int], list[tuple[str, object]]] = {}


def _layer(local, cc: str):
    layer = local._admin_layer(cc)  # noqa: SLF001
    cols = level_columns(layer)
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
        out = list(zip(names, geoms, strict=True))
    _UNITS[key] = out
    return out


class Item(BaseModel):
    op: Literal["fill", "stroke", "erase", "shape"]
    name: str = ""
    slot: int = 0  # index of the category's color in COLORS (wraps around)
    level: int = 0
    id: int = 0
    width: float = 0.0  # stroke width, degrees of latitude
    points: list[list[float]] = []  # stroke path, lon/lat
    extra: str = ""  # image (data URI) of the category; sent on its first item
    extra_blur: bool = False  # blur that image on the card until hovered (sent with `extra`)
    extra_max_height: int | None = None  # px cap for that image on the card (sent with `extra`)
    geometry: dict | None = (
        None  # shape: GeoJSON of an area of a saved drawing that is being edited
    )
    gid: str = ""  # shape: id the server keeps the geometry under, so the browser sends the geometry only once


def _stroke(points: list[list[float]], width: float):
    k = math.cos(math.radians(points[0][1])) or 1e-6
    pts = [(p[0] * k, p[1]) for p in points]
    line = shapely.Point(pts[0]) if len(pts) == 1 else shapely.LineString(pts)
    g = line.buffer(max(width, 1e-6) / 2, quad_segs=4)
    return shapely.transform(g, lambda c: c / np.array([k, 1.0]))


# ── painting: everything drawn so far, kept as polygon parts so a change only touches what it overlaps ──

_DEAD = (math.inf, math.inf, -math.inf, -math.inf)  # bounds of a removed part: no box ever meets it
_STATE_LOCK = threading.Lock()
_STATES: OrderedDict[int, "_Canvas"] = (
    OrderedDict()
)  # chain hash of a list of items -> the canvas it produces
_STATES_MAX = 8
_GEOM_LOCK = threading.Lock()
_GEOMS: OrderedDict[str, object] = (
    OrderedDict()
)  # gid -> exact geometry of a shape the browser already sent
_GEOMS_MAX = 10000
_LANDS: dict[str, object] = {}


def _land(local, cc: str):
    """The land strokes are clipped to: the union of the first-level units, i.e. exactly the land the fills
    cover. (The national outline is a different, simplified line, so clipping strokes to it leaves wedges
    between a stroke and the fills next to it.).
    """
    if cc not in _LANDS:
        try:
            units = _units(local, cc, 1)
        except HTTPException:
            units = []
        land = shapely.union_all([g for _, g in units]) if units else _outline(local, cc)
        if land is None or land.is_empty:
            raise HTTPException(422, f"No local outline for {cc}.")
        _LANDS[cc] = land
    return _LANDS[cc]


def _remember(gid: str, geom) -> None:
    with _GEOM_LOCK:
        _GEOMS[gid] = geom
        _GEOMS.move_to_end(gid)
        while len(_GEOMS) > _GEOMS_MAX:
            _GEOMS.popitem(last=False)


def _shape_geom(it: Item):
    """Geometry of a shape item: the one remembered under its gid, else the one it carries (then remembered)."""
    if it.gid:
        with _GEOM_LOCK:
            g = _GEOMS.get(it.gid)
            if g is not None:
                _GEOMS.move_to_end(it.gid)
                return g
    if not it.geometry:
        raise HTTPException(410, "The server forgot a shape of this drawing. Try again.")
    g = shapely.make_valid(shapely.from_geojson(json.dumps(it.geometry)))
    if it.gid:
        _remember(it.gid, g)
    return g


def _poly_list(g) -> list:
    out, stack = [], [g]
    while stack:
        x = stack.pop()
        if x is None or x.is_empty:
            continue
        if x.geom_type == "Polygon":
            out.append(x)
        elif hasattr(x, "geoms"):
            stack.extend(x.geoms)
    return out


class _Canvas:
    """The painted result as separate polygon parts (geometry, category, bounding box), kept in numpy arrays.

    Painting something subtracts it from the parts of the OTHER categories it really overlaps. Those are found with
    the bounding boxes first, so the cost of a change does not grow with the number of categories or areas.
    Parts are never simplified, snapped or clipped: they are the units' own geometry minus what was painted over.
    """

    def __init__(self) -> None:
        self.geoms = np.empty(256, dtype=object)
        self.cats = np.full(256, -1, dtype=np.int64)  # category id of each part, -1 = removed
        self.bounds = np.full((256, 4), _DEAD, dtype=float)
        self.n = 0
        self.ids: dict[str, int] = {}  # category name -> id, in the order they were first painted
        self.extras: dict[str, str] = {}
        self.looks: dict[
            str, tuple[bool, int | None]
        ] = {}  # name -> (blur, max height) of its image
        self.slots: dict[str, int] = {}

    def copy(self) -> "_Canvas":
        c = _Canvas.__new__(_Canvas)
        c.geoms = self.geoms.copy()
        c.cats = self.cats.copy()
        c.bounds = self.bounds.copy()
        c.n = self.n
        c.ids = dict(self.ids)
        c.extras = dict(self.extras)
        c.looks = dict(self.looks)
        c.slots = dict(self.slots)
        return c

    def _room(self, extra: int) -> None:
        if self.n + extra <= len(self.geoms):
            return
        old = self.n
        live = self.cats[:old] >= 0
        k = int(live.sum())
        if k <= 0.75 * old:  # plenty of removed parts: compact instead of growing
            self.geoms[:k] = self.geoms[:old][live]
            self.cats[:k] = self.cats[:old][live]
            self.bounds[:k] = self.bounds[:old][live]
            self.geoms[k:old] = None
            self.cats[k:old] = -1
            self.bounds[k:old] = _DEAD
            self.n = k
            if k + extra <= len(self.geoms):
                return
        size = max(2 * len(self.geoms), self.n + extra)
        geoms = np.empty(size, dtype=object)
        geoms[: self.n] = self.geoms[: self.n]
        cats = np.full(size, -1, dtype=np.int64)
        cats[: self.n] = self.cats[: self.n]
        bounds = np.full((size, 4), _DEAD, dtype=float)
        bounds[: self.n] = self.bounds[: self.n]
        self.geoms, self.cats, self.bounds = geoms, cats, bounds

    def paint(self, geom, name: str | None) -> None:
        """Paint `geom` in category `name`; name None erases it from every category."""
        parts = _poly_list(geom)
        if not parts:
            return
        cut = geom if geom.geom_type in {"Polygon", "MultiPolygon"} else shapely.union_all(parts)
        cb = shapely.bounds(cut)
        cat = None if name is None else self.ids.setdefault(name, len(self.ids))
        n = self.n
        if n:
            b = self.bounds[:n]
            hit = (b[:, 0] <= cb[2]) & (b[:, 2] >= cb[0]) & (b[:, 1] <= cb[3]) & (b[:, 3] >= cb[1])
            if cat is not None:
                hit &= self.cats[:n] != cat
            idx = np.flatnonzero(hit)
            if len(idx):
                shapely.prepare(cut)
                idx = idx[shapely.intersects(cut, self.geoms[idx])]
            if len(
                idx
            ):  # parts that only touch the new shape along an edge are left exactly as they are
                idx = idx[shapely.relate_pattern(cut, self.geoms[idx], "T********")]
            if len(idx):
                new = shapely.difference(self.geoms[idx], cut)
                gone = shapely.is_empty(new)
                alive = idx[~gone]
                self.geoms[alive] = new[~gone]
                self.bounds[alive] = shapely.bounds(new[~gone])
                dead = idx[gone]
                self.geoms[dead] = None
                self.cats[dead] = -1
                self.bounds[dead] = _DEAD
        if cat is None:
            return
        self._room(len(parts))
        arr = np.empty(len(parts), dtype=object)
        arr[:] = parts
        k = self.n
        self.geoms[k : k + len(parts)] = arr
        self.cats[k : k + len(parts)] = cat
        self.bounds[k : k + len(parts)] = shapely.bounds(arr)
        self.n = k + len(parts)

    def at(self, pt) -> int | None:
        """Id of the category painted on a point (None when nothing is)."""
        b = self.bounds[: self.n]
        idx = np.flatnonzero(
            (b[:, 0] <= pt.x) & (b[:, 2] >= pt.x) & (b[:, 1] <= pt.y) & (b[:, 3] >= pt.y)
        )
        if len(idx):
            ok = shapely.covers(self.geoms[idx], pt)
            if ok.any():
                return int(self.cats[idx[ok][0]])
        return None

    def near(self, bounds, cat: int | None = None, other: bool = False) -> np.ndarray:
        """The parts whose box meets `bounds` (only those of category `cat` when given, or all but those when `other`)."""
        b = self.bounds[: self.n]
        hit = (
            (b[:, 0] <= bounds[2])
            & (b[:, 2] >= bounds[0])
            & (b[:, 1] <= bounds[3])
            & (b[:, 3] >= bounds[1])
        )
        if cat is not None:
            hit &= (self.cats[: self.n] != cat) if other else (self.cats[: self.n] == cat)
        return self.geoms[: self.n][hit]

    def shapes(self) -> dict[str, object]:
        """One geometry per category (its parts united), in the order the categories were first painted."""
        live = np.flatnonzero(self.cats[: self.n] >= 0)
        if not len(live):
            return {}
        order = live[np.argsort(self.cats[live], kind="stable")]
        cats = self.cats[order]
        first = np.flatnonzero(np.r_[True, cats[1:] != cats[:-1]])
        names = {i: nm for nm, i in self.ids.items()}
        out: dict[str, object] = {}
        for s, e in zip(first, np.r_[first[1:], len(order)], strict=True):
            parts = self.geoms[order[s:e]]
            out[names[int(cats[s])]] = parts[0] if len(parts) == 1 else shapely.union_all(parts)
        return out


def _apply(canvas: _Canvas, local, cc: str, it: Item) -> None:
    if it.op == "shape" or (it.op == "erase" and (it.geometry or it.gid)):
        if not (it.geometry or it.gid):
            return
        geom = _shape_geom(it)  # a shape is exactly what was filled: never clipped to anything
    elif it.op == "fill" or (it.op == "erase" and not it.points):
        units = _units(local, cc, it.level)
        if not 0 <= it.id < len(units):
            return
        geom = units[it.id][1]
    else:
        pts = [p for p in it.points if len(p) >= 2]
        if not pts:
            return
        stroke = _stroke(pts, it.width)
        geom = shapely.intersection(stroke, shapely.clip_by_rect(_land(local, cc), *stroke.bounds))
    if geom.is_empty:
        return
    if it.op == "erase":
        canvas.paint(geom, None)
        return
    name = it.name.strip()
    if not name:
        return
    canvas.slots.setdefault(name, it.slot)
    if it.extra:
        canvas.extras[name] = it.extra
        canvas.looks[name] = (it.extra_blur, it.extra_max_height)
    canvas.paint(geom, name)


def _sig(it: Item) -> tuple:
    """What an item contributes to the result (shapes by gid, so their geometry is not hashed again)."""
    geo = it.gid or (hash(json.dumps(it.geometry)) if it.geometry else 0)
    pts = hash(tuple(map(tuple, it.points))) if it.points else 0
    return (it.op, it.name.strip(), it.slot, it.level, it.id, it.width, geo, pts,
            hash(it.extra), it.extra_blur, it.extra_max_height)  # fmt: skip


def _canvas(local, cc: str, items: list[Item]) -> _Canvas:
    """The canvas the items produce. It continues from the longest list of items seen before (the browser sends the
    whole list every time, and it nearly always is the previous one plus a change), so only the new items are replayed.
    The returned canvas may be shared: read it, never paint on it.
    """
    if not items:
        return _Canvas()
    keys, h = [], hash(("canvas", cc))
    for it in items:
        h = hash((h, _sig(it)))
        keys.append(h)
    with _STATE_LOCK:
        base, start = None, 0
        for i in range(len(keys), 0, -1):
            base = _STATES.get(keys[i - 1])
            if base is not None:
                start = i
                _STATES.move_to_end(keys[i - 1])
                break
        if start == len(items):
            return base
        canvas = base.copy() if base is not None else _Canvas()
        for it in items[start:]:
            _apply(canvas, local, cc, it)
        _STATES[keys[-1]] = canvas
        while len(_STATES) > _STATES_MAX:
            _STATES.popitem(last=False)
        return canvas
