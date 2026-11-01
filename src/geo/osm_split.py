"""osm_split.py — Cut an .osm.pbf into one file per polygon, in pure Python (pyosmium + shapely, no osmium-tool).

One pass over the file, one writer per polygon. Everything is cut at the polygon border:
  node       kept by every polygon that contains it
  way        keeps only the nodes inside (so a road is cut at the border); dropped with fewer than 2 left
  relation   keeps only the member ways that survived; relations with none left are dropped
             (node and relation members are not kept)
The polygons are widened by BUFFER so border nodes that sit exactly on the line stay in, which keeps the
boundaries along the border closed. Needs node locations while reading (kept in memory by pyosmium).
"""

from __future__ import annotations

import math
from array import array
from typing import TYPE_CHECKING

import numpy as np
import shapely

if TYPE_CHECKING:
    from pathlib import Path

CELL = (
    0.02  # degrees: nodes are looked up per cell; only cells touching a border need the exact test
)
BUFFER = 0.002  # degrees (~200 m)
MAX_CACHE = 3_000_000


class _Router:
    """Which polygons contain a point. Cells wholly inside / outside are answered from a cache."""

    def __init__(self, geoms: list) -> None:
        self.geoms = geoms
        self.bounds = [g.bounds for g in geoms]
        self.cache: dict[tuple[int, int], tuple[tuple, tuple]] = {}

    def _cell(self, cx: int, cy: int) -> tuple[tuple, tuple]:
        x0, y0 = cx * CELL, cy * CELL
        x1, y1 = x0 + CELL, y0 + CELL
        box = shapely.box(x0, y0, x1, y1)
        inside, mixed = [], []
        for k, (g, b) in enumerate(zip(self.geoms, self.bounds, strict=True)):
            if x1 < b[0] or x0 > b[2] or y1 < b[1] or y0 > b[3]:
                continue
            if shapely.contains(g, box):
                inside.append(k)
            elif shapely.intersects(g, box):
                mixed.append(k)
        return tuple(inside), tuple(mixed)

    def where(self, lon: float, lat: float):
        key = (math.floor(lon / CELL), math.floor(lat / CELL))
        ent = self.cache.get(key)
        if ent is None:
            if len(self.cache) >= MAX_CACHE:
                self.cache.clear()
            ent = self.cache[key] = self._cell(*key)
        inside, mixed = ent
        if not mixed:
            return inside
        return (*inside, *(k for k in mixed if shapely.contains_xy(self.geoms[k], lon, lat)))


def _isin(sorted_arr: np.ndarray, refs: np.ndarray) -> np.ndarray:
    if not len(sorted_arr):
        return np.zeros(len(refs), bool)
    i = np.searchsorted(sorted_arr, refs)
    i[i == len(sorted_arr)] = len(sorted_arr) - 1
    return sorted_arr[i] == refs


def split_pbf(src: Path, polys: dict[str, object], out_dir: Path) -> dict[str, dict]:
    """polys: {file label: lon/lat polygon}. Writes out_dir/<label>.osm.pbf for each; returns the counts."""
    import osmium

    mutable = osmium.osm.mutable
    labels = list(polys)
    geoms = []
    for g in polys.values():
        g = shapely.simplify(shapely.buffer(g, BUFFER), BUFFER / 4)
        shapely.prepare(g)
        geoms.append(g)
    router = _Router(geoms)

    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [out_dir / f"{label}.osm.pbf" for label in labels]
    for p in paths:
        p.unlink(missing_ok=True)
    writers = [osmium.SimpleWriter(str(p)) for p in paths]
    counts = [{"nodes": 0, "ways": 0, "relations": 0} for _ in labels]
    written = [array("q") for _ in labels]  # ids of the ways written, per polygon
    ways_np: list[np.ndarray] | None = None
    all_ways = np.empty(0, np.int64)
    try:
        fp = osmium.FileProcessor(
            str(src), osmium.osm.NODE | osmium.osm.WAY | osmium.osm.RELATION
        ).with_locations()
        for o in fp:
            if o.is_node():
                loc = o.location
                if not loc.valid():
                    continue
                for k in router.where(loc.lon, loc.lat):
                    writers[k].add_node(o)
                    counts[k]["nodes"] += 1
            elif o.is_way():
                nodes = o.nodes
                total = len(nodes)
                by_k: dict[int, list[int]] = {}
                for n in nodes:
                    loc = n.location
                    if not loc.valid():
                        continue
                    for k in router.where(loc.lon, loc.lat):
                        by_k.setdefault(k, []).append(n.ref)
                for k, refs in by_k.items():
                    if len(refs) < 2:
                        continue
                    writers[k].add_way(o if len(refs) == total else mutable.Way(o, nodes=refs))
                    written[k].append(o.id)
                    counts[k]["ways"] += 1
            else:
                if ways_np is None:  # the ways are all written by now: freeze them for lookups
                    ways_np = [np.unique(np.frombuffer(a, dtype=np.int64)) for a in written]
                    all_ways = np.unique(np.concatenate(ways_np)) if ways_np else all_ways
                members = [(str(m.type)[:1], m.ref, m.role) for m in o.members]
                refs = np.array([r for t, r, _ in members if t == "w"], dtype=np.int64)
                if not len(refs) or not _isin(all_ways, refs).any():
                    continue
                for k, arr in enumerate(ways_np):
                    ok = _isin(arr, refs)
                    if not ok.any():
                        continue
                    have = set(refs[ok].tolist())
                    keep = [m for m in members if m[0] == "w" and m[1] in have]
                    writers[k].add_relation(
                        o if len(keep) == len(members) else mutable.Relation(o, members=keep)
                    )
                    counts[k]["relations"] += 1
    finally:
        for w in writers:
            try:
                w.close()
            except Exception:  # noqa: BLE001
                pass
    return {label: {"path": str(p), **c} for label, p, c in zip(labels, paths, counts, strict=True)}
