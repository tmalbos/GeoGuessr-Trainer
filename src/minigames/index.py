"""index.py — per-country road-density index, so a valid window is found by lookup, never by trial and error.

Build (once per country, from <country>.gpkg):  road length is accumulated into a coarse grid (about 1 km cells).
Use (in memory): two summed-area tables (road length, occupied cells) give the road length and the coverage of ANY
rectangle in O(1). Every window of the requested size is tested at once with numpy, and one valid window is picked
at random. The number of valid windows per size is stored, so countries that cannot offer a place of that size are
never even considered.

A window is "valid" (not sparse) when, for its size:
  - mean road density >= MIN_DENSITY km of road per km2, and
  - at least MIN_OCCUPIED of its cells hold >= OCC_CELL_M metres of road (roads everywhere, not one dense corner).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterator
from functools import lru_cache
from pathlib import Path

import numpy as np

from src.minigames.gpkg import connect, parse

DATA_DIR = Path(__file__).parents[2] / "data" / "minigames"

# Selectable window sizes (km). The UI slider walks through these.
SIZES_KM = (2, 3, 4, 6, 8, 11, 15)

# ── tunables ──
MIN_DENSITY = 6.0  # km of road per km2
MIN_OCCUPIED = 0.70  # share of cells that must hold some road
OCC_CELL_M = 500.0  # metres of road that make a cell "occupied"
WEIGHTS = {"track": 0.0, "service": 0.5}  # how much a road class counts towards density (default 1)

CELL_DEG = 0.01  # grid cell, about 1.1 km. Grows for huge countries so the grid stays <= MAX_CELLS.
MAX_CELLS = 16_000_000
M_LAT = 110_574.0  # metres per degree of latitude
M_LON = 111_320.0  # metres per degree of longitude at the equator

# GRID_FP changes -> the grid must be rebuilt from the gpkg. COUNT_FP changes -> only the counts are recomputed.
GRID_FP = f"v1;{CELL_DEG};{MAX_CELLS};{sorted(WEIGHTS.items())}"
COUNT_FP = f"v1;{SIZES_KM};{MIN_DENSITY};{MIN_OCCUPIED};{OCC_CELL_M}"

Progress = Callable[[str], None]


def index_path(name: str) -> Path:
    return DATA_DIR / f"{name}.index.npz"


def gpkg_path(name: str) -> Path:
    return DATA_DIR / f"{name}.gpkg"


# ── building the grid ────────────────────────────────────────────────────────────────────────────────────────────


def build_grid(
    gpkg: Path, progress: Progress | None = None
) -> tuple[np.ndarray, float, float, float]:
    """Accumulate road length (metres, weighted) into a lon/lat grid. Returns (grid[ny, nx], lon0, lat0, cell)."""
    say = progress or (lambda _m: None)
    con = connect(gpkg)
    try:
        b = con.execute(
            "SELECT min(minx), min(miny), max(maxx), max(maxy) FROM rtree_roads_geom"
        ).fetchone()
        if b is None or b[0] is None:
            msg = f"{gpkg.name} has no roads"
            raise ValueError(msg)
        minx, miny, maxx, maxy = b
        k = max(
            1,
            math.ceil(
                math.sqrt(((maxx - minx) / CELL_DEG) * ((maxy - miny) / CELL_DEG) / MAX_CELLS)
            ),
        )
        cell = CELL_DEG * k
        lon0, lat0 = math.floor(minx / cell) * cell, math.floor(miny / cell) * cell
        nx, ny = int((maxx - lon0) / cell) + 1, int((maxy - lat0) / cell) + 1
        grid = np.zeros(nx * ny, dtype=np.float64)
        step = cell * M_LAT * 0.5  # long segments are cut into pieces of at most half a cell
        total = con.execute("SELECT count(*) FROM roads").fetchone()[0]
        say(f"reading {total:,} roads into a {nx}x{ny} grid (cell {cell * M_LAT / 1000:.2f} km)")

        done = 0
        cur = con.execute("SELECT geom, highway FROM roads")
        while True:
            rows = cur.fetchmany(50_000)
            if not rows:
                break
            done += len(rows)
            arrs: list[np.ndarray] = []
            wts: list[float] = []
            for blob, hw in rows:
                w = WEIGHTS.get(hw, 1.0)
                if w <= 0:
                    continue
                for a in parse(blob):
                    if len(a) > 1:
                        arrs.append(a)
                        wts.append(w)
            if arrs:
                _accumulate(grid, arrs, np.asarray(wts), lon0, lat0, cell, nx, ny, step)
            if done % 500_000 < 50_000:
                say(f"  {done:,} / {total:,}")
    finally:
        con.close()
    return grid.reshape(ny, nx).astype(np.float32), lon0, lat0, cell


def _accumulate(grid, arrs, wts, lon0, lat0, cell, nx, ny, step) -> None:
    counts = np.fromiter((len(a) for a in arrs), np.int64, len(arrs))
    xy = np.concatenate(arrs)
    feat = np.repeat(np.arange(len(arrs)), counts)[:-1]
    keep = np.ones(len(xy) - 1, dtype=bool)
    keep[np.cumsum(counts)[:-1] - 1] = (
        False  # the "segment" from one feature's last point to the next one's first
    )
    x0, y0, x1, y1 = xy[:-1, 0][keep], xy[:-1, 1][keep], xy[1:, 0][keep], xy[1:, 1][keep]
    w = wts[feat][keep]
    dx = (x1 - x0) * M_LON * np.cos(np.radians((y0 + y1) / 2))
    dy = (y1 - y0) * M_LAT
    ln = np.hypot(dx, dy)
    k = np.maximum(1, np.ceil(ln / step)).astype(np.int64)
    rep = np.repeat(np.arange(len(ln)), k)
    j = np.arange(len(rep)) - np.repeat(np.cumsum(k) - k, k)
    t = (j + 0.5) / k[rep]
    px = x0[rep] + (x1 - x0)[rep] * t
    py = y0[rep] + (y1 - y0)[rep] * t
    piece = (ln / k)[rep] * w[rep]
    ix = np.clip(((px - lon0) / cell).astype(np.int64), 0, nx - 1)
    iy = np.clip(((py - lat0) / cell).astype(np.int64), 0, ny - 1)
    u, inv = np.unique(iy * nx + ix, return_inverse=True)
    grid[u] += np.bincount(inv, weights=piece)


# ── the index in memory ──────────────────────────────────────────────────────────────────────────────────────────


class PlaceIndex:
    """Summed-area tables over the grid: road length and occupied-cell count of any rectangle in O(1)."""

    def __init__(self, grid: np.ndarray, lon0: float, lat0: float, cell: float) -> None:
        self.ny, self.nx = grid.shape
        self.lon0, self.lat0, self.cell = lon0, lat0, cell
        self.len_sat = np.zeros((self.ny + 1, self.nx + 1), dtype=np.float64)
        self.len_sat[1:, 1:] = np.cumsum(np.cumsum(grid, axis=0, dtype=np.float64), axis=1)
        self.occ_sat = np.zeros((self.ny + 1, self.nx + 1), dtype=np.int32)
        self.occ_sat[1:, 1:] = np.cumsum(
            np.cumsum(grid >= OCC_CELL_M, axis=0, dtype=np.int32), axis=1
        )

    def _shape(self, size_km: float) -> tuple[int, np.ndarray, np.ndarray, np.ndarray]:
        """Window height in cells, and per candidate row: width in cells, km per cell (lon), window area (km2)."""
        cell_lat_km = self.cell * M_LAT / 1000
        wy = max(1, round(size_km / cell_lat_km))
        rows = np.arange(0, max(0, self.ny - wy + 1))
        lat_c = self.lat0 + (rows + wy / 2) * self.cell
        cell_lon_km = self.cell * M_LON * np.cos(np.radians(lat_c)) / 1000
        wx = np.maximum(1, np.rint(size_km / cell_lon_km)).astype(np.int64)
        area = (wx * cell_lon_km) * (wy * cell_lat_km)
        return wy, rows, wx, area

    def scan(self, size_km: float) -> Iterator[tuple[np.ndarray, np.ndarray, int]]:
        """Yield (rows, cols, wx) chunks: every window of that size that is valid, as top-left cell indices."""
        wy, rows, wx_r, area = self._shape(size_km)
        if not len(rows):
            return
        for wx in np.unique(wx_r):
            wx = int(wx)
            if wx > self.nx:
                continue
            sel = wx_r == wx
            grp, ar = rows[sel], area[sel]
            cols = np.arange(0, self.nx - wx + 1)
            need = MIN_OCCUPIED * wx * wy
            for lo in range(0, len(grp), 128):  # chunks keep the temporary arrays small
                a = grp[lo : lo + 128][:, None]
                b = a + wy
                c = cols[None, :]
                d = c + wx
                s, o = self.len_sat, self.occ_sat
                length_m = s[b, d] - s[b, c] - s[a, d] + s[a, c]
                occupied = o[b, d] - o[b, c] - o[a, d] + o[a, c]
                ok = (length_m / 1000.0 >= MIN_DENSITY * ar[lo : lo + 128][:, None]) & (
                    occupied >= need
                )
                ri, ci = np.nonzero(ok)
                if len(ri):
                    yield grp[lo : lo + 128][ri], cols[ci], wx

    def count_valid(self, size_km: float) -> int:
        return sum(len(r) for r, _c, _w in self.scan(size_km))

    def pick(
        self, size_km: float, rng: np.random.Generator | None = None
    ) -> tuple[float, float] | None:
        """Centre (lat, lon) of a random valid window, or None if the country has none of that size.

        Valid windows overlap heavily inside big cities, so choosing one uniformly would always land in the biggest
        city. Instead the valid windows are grouped in blocks about twice the window size, a random block is
        chosen, then a random window inside it. Different places get a fair chance.
        """
        rng = rng or np.random.default_rng()
        wy = self._shape(size_km)[0]
        parts = list(self.scan(size_km))
        if not parts:
            return None
        r = np.concatenate([p[0] for p in parts])
        c = np.concatenate([p[1] for p in parts])
        wx = np.concatenate([np.full(len(p[0]), p[2]) for p in parts])
        blk = 2 * max(int(wx.max()), wy)
        key = (r // blk) * (self.nx // blk + 2) + c // blk
        chosen = rng.choice(np.unique(key))
        i = rng.choice(np.flatnonzero(key == chosen))
        return (
            self.lat0 + (int(r[i]) + wy / 2) * self.cell,
            self.lon0 + (int(c[i]) + int(wx[i]) / 2) * self.cell,
        )


def compute_counts(idx: PlaceIndex) -> np.ndarray:
    return np.array([idx.count_valid(s) for s in SIZES_KM], dtype=np.int64)


# ── files ────────────────────────────────────────────────────────────────────────────────────────────────────────


def _save(
    name: str, grid: np.ndarray, lon0: float, lat0: float, cell: float, counts: np.ndarray
) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f"{name}.index.tmp.npz"
    np.savez_compressed(
        tmp,
        grid=grid,
        meta=np.array([lon0, lat0, cell]),
        counts=counts,
        grid_fp=np.array(GRID_FP),
        count_fp=np.array(COUNT_FP),
    )
    Path(tmp).replace(index_path(name))


def build_index(name: str, progress: Progress | None = None) -> None:
    """Read <name>.gpkg once, store the density grid and the valid-window counts. Takes a while for big countries."""
    say = progress or (lambda _m: None)
    grid, lon0, lat0, cell = build_grid(gpkg_path(name), say)
    say("counting valid windows per size")
    counts = compute_counts(PlaceIndex(grid, lon0, lat0, cell))
    _save(name, grid, lon0, lat0, cell, counts)
    say(
        "index ready: " + ", ".join(f"{s} km={n:,}" for s, n in zip(SIZES_KM, counts, strict=False))
    )


def index_state(name: str) -> str:
    """'missing', 'stale' (grid must be rebuilt), 'recount' (only the counts are out of date) or 'ok'."""
    f = index_path(name)
    if not f.exists():
        return "missing"
    try:
        with np.load(f) as z:
            if str(z["grid_fp"]) != GRID_FP:
                return "stale"
            return "ok" if str(z["count_fp"]) == COUNT_FP else "recount"
    except Exception:  # noqa: BLE001 - a corrupt file is as good as a missing one
        return "stale"


def _recount(name: str) -> np.ndarray:
    with np.load(index_path(name)) as z:
        grid, (lon0, lat0, cell) = z["grid"], z["meta"]
    counts = compute_counts(PlaceIndex(grid, float(lon0), float(lat0), float(cell)))
    _save(name, grid, float(lon0), float(lat0), float(cell), counts)
    return counts


@lru_cache(maxsize=2)
def load_index(name: str) -> PlaceIndex:
    with np.load(index_path(name)) as z:
        grid, (lon0, lat0, cell) = z["grid"], z["meta"]
    return PlaceIndex(grid, float(lon0), float(lat0), float(cell))


def list_countries() -> dict:
    """What the UI needs: window sizes, indexed countries with their valid-window counts, and unindexed ones."""
    countries, pending = [], []
    for g in sorted(DATA_DIR.glob("*.gpkg")):
        name = g.stem
        if "." in name:  # <name>.raw / <name>.partial leftovers
            continue
        state = index_state(name)
        if state in {"missing", "stale"}:
            pending.append(name)
            continue
        if state == "recount":
            counts = _recount(name)
        else:
            with np.load(index_path(name)) as z:
                counts = z["counts"]
        countries.append(
            {
                "name": name,
                "valid": {str(s): int(n) for s, n in zip(SIZES_KM, counts, strict=False)},
            }
        )
    return {"sizes": list(SIZES_KM), "countries": countries, "pending": pending}
