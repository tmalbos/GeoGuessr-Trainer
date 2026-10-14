"""gpkg.py — read-only GeoPackage access with the standard library + numpy (no GDAL needed at runtime)."""

from __future__ import annotations

import sqlite3
import struct
from pathlib import Path

import numpy as np

_ENVELOPE_BYTES = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}


def connect(path: Path) -> sqlite3.Connection:
    """Open a GeoPackage read-only. One connection per thread."""
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def _kind(t: int) -> tuple[int, int]:
    """WKB type code -> (base type 1..7, number of coordinates per vertex). Handles ISO and EWKB codes."""
    if t & 0xE0000000:
        return t & 0xFF, 2 + bool(t & 0x80000000) + bool(t & 0x40000000)
    return t % 1000, (2, 3, 3, 4)[t // 1000]


def _read_coords(buf: bytes, off: int, e: str, nd: int, out: list[np.ndarray]) -> int:
    (n,) = struct.unpack_from(e + "I", buf, off)
    off += 4
    a = np.frombuffer(buf, dtype=e + "f8", count=n * nd, offset=off).reshape(n, nd)
    out.append(a[:, :2].astype(np.float64, copy=False))
    return off + 8 * n * nd


def _read(buf: bytes, off: int, out: list[np.ndarray]) -> int:
    e = "<" if buf[off] == 1 else ">"
    (t,) = struct.unpack_from(e + "I", buf, off + 1)
    off += 5
    base, nd = _kind(t)
    if base == 1:  # point
        return off + 8 * nd
    if base == 2:  # linestring
        return _read_coords(buf, off, e, nd, out)
    if base == 3:  # polygon: every ring becomes one array
        (nr,) = struct.unpack_from(e + "I", buf, off)
        off += 4
        for _ in range(nr):
            off = _read_coords(buf, off, e, nd, out)
        return off
    (ng,) = struct.unpack_from(e + "I", buf, off)  # multi* / collection
    off += 4
    for _ in range(ng):
        off = _read(buf, off, out)
    return off


def parse(blob: bytes) -> list[np.ndarray]:
    """GeoPackage geometry blob -> list of (n, 2) float64 arrays (lines, or polygon rings)."""
    flags = blob[3]
    if flags & 0x10:  # empty geometry
        return []
    out: list[np.ndarray] = []
    _read(blob, 8 + _ENVELOPE_BYTES.get((flags >> 1) & 7, 0), out)
    return out


def window_rows(
    con: sqlite3.Connection,
    table: str,
    bbox: tuple[float, float, float, float],
    columns: tuple[str, ...] = (),
):
    """Features whose bounding box touches bbox (minx, miny, maxx, maxy), through the GeoPackage R-tree.

    Each row: (geom_blob, *columns, feat_minx, feat_maxx, feat_miny, feat_maxy).
    """
    minx, miny, maxx, maxy = bbox
    cols = "".join(f", t.{c}" for c in columns)
    sql = (
        f"SELECT t.geom{cols}, r.minx, r.maxx, r.miny, r.maxy FROM {table} t "
        f"JOIN rtree_{table}_geom r ON t.fid = r.id "
        "WHERE r.maxx >= ? AND r.minx <= ? AND r.maxy >= ? AND r.miny <= ?"
    )
    return con.execute(sql, (minx, maxx, miny, maxy))
