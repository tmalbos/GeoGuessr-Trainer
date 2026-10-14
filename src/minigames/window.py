"""window.py — read one square window of a country GeoPackage and pack it into a compact binary payload.

Local coordinates: metres, origin at the window centre, +x east, +y north. Everything is sent as typed arrays:
  per layer: `start` (uint32, part offsets, count+1 values) and `xy` (float32, x0 y0 x1 y1 ...), plus per-part
  attribute arrays (`cls`, `flag`, `kind`) where the layer has them.

Payload = [uint32 header length][JSON header, space padded to a multiple of 4][binary arrays, 4-byte aligned].
The header lists each array as {t: f32|u32|u8, off, n}, offsets relative to the end of the header.
"""

from __future__ import annotations

import json
import math
import sqlite3
import struct
from typing import TYPE_CHECKING

import numpy as np

from src.minigames.gpkg import connect, parse, window_rows
from src.minigames.index import M_LAT, M_LON
from src.minigames.ocean import chain_lines, sea_rings

if TYPE_CHECKING:
    from collections.abc import Callable

ROAD_CLASS = {
    "track": 0,
    "service": 1,
    "living_street": 2,
    "residential": 2,
    "unclassified": 2,
    "tertiary": 3,
    "tertiary_link": 3,
    "secondary": 4,
    "secondary_link": 4,
    "primary": 5,
    "primary_link": 5,
    "trunk": 6,
    "trunk_link": 6,
    "motorway": 7,
    "motorway_link": 7,
}
WATERWAY_KIND = {"river": 0, "canal": 1, "stream": 2}
FLAG_BRIDGE, FLAG_TUNNEL, FLAG_ROUNDABOUT = 1, 2, 4

BBox = tuple[float, float, float, float]  # minx, miny, maxx, maxy (degrees)


def _runs(a: np.ndarray, bbox: BBox) -> list[np.ndarray]:
    """Pieces of a polyline made of the segments whose bounding box touches bbox (whole segments are kept)."""
    minx, miny, maxx, maxy = bbox
    x, y = a[:, 0], a[:, 1]
    keep = (
        (np.maximum(x[:-1], x[1:]) >= minx)
        & (np.minimum(x[:-1], x[1:]) <= maxx)
        & (np.maximum(y[:-1], y[1:]) >= miny)
        & (np.minimum(y[:-1], y[1:]) <= maxy)
    )
    if keep.all():
        return [a]
    idx = np.flatnonzero(keep)
    if not len(idx):
        return []
    cut = np.flatnonzero(np.diff(idx) > 1)
    first = np.r_[idx[0], idx[cut + 1]]
    last = np.r_[idx[cut], idx[-1]]
    return [a[s : e + 2] for s, e in zip(first, last, strict=False)]


def _flatten(
    parts: list[np.ndarray], to_local: Callable[[np.ndarray], np.ndarray]
) -> tuple[np.ndarray, np.ndarray]:
    start = np.zeros(len(parts) + 1, dtype=np.uint32)
    if not parts:
        return start, np.zeros(0, dtype=np.float32)
    start[1:] = np.cumsum(np.fromiter((len(p) for p in parts), np.int64, len(parts)))
    return start, to_local(np.concatenate(parts)).astype(np.float32).ravel()


def _line_layer(con, table, columns, bbox, to_local, attrs: Callable[[tuple], tuple]) -> dict:
    """Lines clipped to the window. attrs(row_values) -> per-part attribute tuple."""
    minx, miny, maxx, maxy = bbox
    parts: list[np.ndarray] = []
    meta: list[tuple] = []
    try:
        rows = list(window_rows(con, table, bbox, columns))
    except sqlite3.OperationalError:  # layer missing in this file
        rows = []
    for row in rows:
        vals = row[1 : 1 + len(columns)]
        fminx, fmaxx, fminy, fmaxy = row[1 + len(columns) :]
        inside = fminx >= minx and fmaxx <= maxx and fminy >= miny and fmaxy <= maxy
        at = attrs(vals)
        for a in parse(row[0]):
            if len(a) < 2:
                continue
            for piece in [a] if inside else _runs(a, bbox):
                parts.append(piece)
                meta.append(at)
    start, xy = _flatten(parts, to_local)
    return {"start": start, "xy": xy, "meta": meta}


def _polygon_layer(con, table, bbox, to_local) -> dict:
    """Polygon rings, whole (the canvas clips them). Rings of one polygon are filled together with even-odd."""
    try:
        rows = list(window_rows(con, table, bbox))
    except sqlite3.OperationalError:
        rows = []
    rings = [r for row in rows for r in parse(row[0]) if len(r) >= 4]
    start, xy = _flatten(rings, to_local)
    return {"start": start, "xy": xy}


def _road_attrs(v: tuple) -> tuple[int, int]:
    highway, bridge, tunnel, junction = v
    flag = (FLAG_BRIDGE if bridge not in {None, "", "no"} else 0) | (
        FLAG_TUNNEL if tunnel not in {None, "", "no"} else 0
    )
    if junction in {"roundabout", "circular"}:
        flag |= FLAG_ROUNDABOUT
    return ROAD_CLASS.get(highway, 2), flag


def _rail_attrs(v: tuple) -> tuple[int]:
    _railway, bridge, tunnel = v
    return (
        (FLAG_BRIDGE if bridge not in {None, "", "no"} else 0)
        | (FLAG_TUNNEL if tunnel not in {None, "", "no"} else 0),
    )


def _water_attrs(v: tuple) -> tuple[int]:
    return (WATERWAY_KIND.get(v[0], 0),)


def fetch_window(gpkg, lat_c: float, lon_c: float, size_km: float) -> tuple[dict, dict]:
    """Return (layers, info). layers: name -> {start, xy, [cls, flag, kind]} as numpy arrays."""
    half = size_km * 500.0
    kx, ky = M_LON * math.cos(math.radians(lat_c)), M_LAT
    bbox: BBox = (lon_c - half / kx, lat_c - half / ky, lon_c + half / kx, lat_c + half / ky)
    origin, scale = np.array([lon_c, lat_c]), np.array([kx, ky])

    def to_local(a: np.ndarray) -> np.ndarray:
        return (a - origin) * scale

    con = connect(gpkg)
    try:
        roads = _line_layer(
            con, "roads", ("highway", "bridge", "tunnel", "junction"), bbox, to_local, _road_attrs
        )
        rail = _line_layer(
            con, "rail", ("railway", "bridge", "tunnel"), bbox, to_local, _rail_attrs
        )
        wlines = _line_layer(con, "water_lines", ("waterway",), bbox, to_local, _water_attrs)
        wareas = _polygon_layer(con, "water_areas", bbox, to_local)
        islands = _polygon_layer(
            con, "islands", bbox, to_local
        )  # whole rings that close inside the extract = land
        try:
            coast_rows = list(window_rows(con, "coastline", bbox))
        except sqlite3.OperationalError:
            coast_rows = []
        coast = [to_local(a) for row in coast_rows for a in parse(row[0]) if len(a) >= 2]
    finally:
        con.close()

    sea, land_loops = sea_rings(chain_lines(coast) if coast else [], half)
    sea_start = np.zeros(len(sea) + 1, dtype=np.uint32)
    if sea:
        sea_start[1:] = np.cumsum([len(r) for r in sea])
    sea_layer = {
        "start": sea_start,
        "xy": np.concatenate(sea).astype(np.float32).ravel() if sea else np.zeros(0, np.float32),
    }

    if (
        land_loops
    ):  # rings made of several coastline ways that close inside the window are islands too
        extra_start = np.zeros(len(land_loops) + 1, dtype=np.uint32)
        extra_start[1:] = np.cumsum([len(r) for r in land_loops])
        extra_xy = np.concatenate(land_loops).astype(np.float32).ravel()
        base = int(islands["start"][-1]) if len(islands["start"]) else 0
        islands = {
            "start": np.concatenate([islands["start"], extra_start[1:] + base]).astype(np.uint32),
            "xy": np.concatenate([islands["xy"], extra_xy]),
        }

    def col(layer: dict, i: int, n: int) -> np.ndarray:
        return (
            np.array([m[i] for m in layer["meta"]], dtype=np.uint8) if n else np.zeros(0, np.uint8)
        )

    layers = {
        "sea": sea_layer,
        "islands": islands,
        "water_areas": wareas,
        "water_lines": {
            "start": wlines["start"],
            "xy": wlines["xy"],
            "kind": col(wlines, 0, len(wlines["meta"])),
        },
        "rail": {"start": rail["start"], "xy": rail["xy"], "flag": col(rail, 0, len(rail["meta"]))},
        "roads": {
            "start": roads["start"],
            "xy": roads["xy"],
            "cls": col(roads, 0, len(roads["meta"])),
            "flag": col(roads, 1, len(roads["meta"])),
        },
    }
    return layers, {"half_m": half, "bbox": bbox}


_DTYPES = {np.dtype(np.float32): "f32", np.dtype(np.uint32): "u32", np.dtype(np.uint8): "u8"}


def pack(layers: dict[str, dict], meta: dict) -> bytes:
    """Binary payload (see module docstring)."""
    blob = bytearray()
    arrays: dict[str, dict] = {}
    counts: dict[str, dict] = {}
    for lname, layer in layers.items():
        counts[lname] = {"count": int(len(layer["start"]) - 1), "pts": int(len(layer["xy"]) // 2)}
        for aname, arr in layer.items():
            blob.extend(b"\0" * (-len(blob) % 4))
            arrays[f"{lname}.{aname}"] = {
                "t": _DTYPES[arr.dtype],
                "off": len(blob),
                "n": int(arr.size),
            }
            blob.extend(np.ascontiguousarray(arr).tobytes())
    header = json.dumps(
        {**meta, "layers": counts, "arrays": arrays}, separators=(",", ":")
    ).encode()
    header += b" " * (-len(header) % 4)
    return struct.pack("<I", len(header)) + header + bytes(blob)
