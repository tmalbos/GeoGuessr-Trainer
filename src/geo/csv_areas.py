"""csv_areas.py — A CSV of named groups of subdivisions -> colored areas (one polygon per group).

CSV:  GroupName, Level1, Level2, ..., extra_info
    Level1, Level2, ... are the country's admin levels, finest last (state, subregion, city in the local
    layer; a country without `subregion` skips that level). A row selects every unit that matches the levels
    it fills in, so "Group A, Texas" is the whole state and "Group A, Texas, Harris" only that part.
    When rows overlap, the more specific row wins. Rows with the same GroupName make one area.
    extra_info (optional): text, or the path of an image / SVG (absolute, or relative to the CSV).
    An image is embedded in the result as a small data URI, so the parquet is self-contained.

Colors are automatic: neighbouring areas always get clearly different colors (see assign_colors).
"""

import base64
import colorsys
import csv
import io
import re
from pathlib import Path

import numpy as np
import shapely
from PIL import Image, ImageOps
from shapely import STRtree

from src.geo.simplify import simplify_coverage, tolerance

LEVEL_COLUMNS = ("state", "subregion", "city")
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".avif", ".tif", ".tiff"}
EXTRA_MAX_PX = 640
EXTRA_QUALITY = 80
ADJ_TOL_DEG = 0.005  # areas closer than this (~500 m) count as neighbours
MIN_DISTANCE = 40.0  # Lab distance a color should keep from its neighbours' colors
ABS_PATH = re.compile(r'^"?([a-zA-Z]:[\\/]|/|\\\\)')


class AreasError(Exception):
    """A problem in the CSV the user can fix (shown as a 422)."""


# ── colors ─────────────────────────────────────────────────────────────────

_M = np.array([[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]])
_W = np.array([0.95047, 1.0, 1.08883])


def _lab(rgb) -> np.ndarray:
    c = np.asarray(rgb, float)
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    t = (c @ _M.T) / _W
    f = np.where(t > 0.008856, np.cbrt(t), 7.787 * t + 16 / 116)
    return np.array([116 * f[1] - 16, 500 * (f[0] - f[1]), 200 * (f[1] - f[2])])


def _palette() -> list[tuple[float, float, float]]:
    out = []
    for n, (light, sat) in enumerate(((0.55, 0.65), (0.42, 0.75), (0.68, 0.55))):
        out.extend(colorsys.hls_to_rgb((i + n / 3) / 24, light, sat) for i in range(24))
    return out


_PALETTE = _palette()
_PALETTE_LAB = np.array([_lab(c) for c in _PALETTE])
_PALETTE_HEX = ["#" + "".join(f"{round(v * 255):02x}" for v in c) for c in _PALETTE]


def assign_colors(geoms: list) -> list[str]:
    """One hex color per geometry. Neighbours get colors at least MIN_DISTANCE apart when the palette
    allows it; among the colors that qualify, the least used one wins, so colors spread out evenly.
    """
    n = len(geoms)
    nbrs: list[set[int]] = [set() for _ in range(n)]
    if n > 1:
        arr = np.empty(n, dtype=object)
        arr[:] = geoms
        a, b = STRtree(arr).query(arr, predicate="dwithin", distance=ADJ_TOL_DEG)
        for i, j in zip(a.tolist(), b.tolist(), strict=True):
            if i != j:
                nbrs[i].add(j)
                nbrs[j].add(i)
    chosen = [-1] * n
    used = np.zeros(len(_PALETTE))
    for i in sorted(range(n), key=lambda k: (-len(nbrs[k]), k)):
        near = [chosen[j] for j in nbrs[i] if chosen[j] >= 0]
        if near:
            dist = np.linalg.norm(_PALETTE_LAB[:, None, :] - _PALETTE_LAB[near][None], axis=2).min(
                1
            )
            ok = np.flatnonzero(dist >= min(float(dist.max()), MIN_DISTANCE))
        else:
            ok = np.arange(len(_PALETTE))
        pick = int(ok[np.argmin(used[ok])])
        chosen[i] = pick
        used[pick] += 1
    return [_PALETTE_HEX[c] for c in chosen]


# ── extra_info ─────────────────────────────────────────────────────────────


def _data_uri(path: Path) -> str:
    if path.suffix.lower() == ".svg":
        return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode()
    img = ImageOps.exif_transpose(Image.open(path))
    img.load()
    img.thumbnail((EXTRA_MAX_PX, EXTRA_MAX_PX), Image.LANCZOS)
    img = img.convert("RGBA" if "A" in img.getbands() or "transparency" in img.info else "RGB")
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=EXTRA_QUALITY)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def _extra(value: str, base_dir: Path | None, cache: dict) -> str:
    """Text stays text; the path of an image / SVG becomes a data URI."""
    v = value.strip()
    if not v:
        return ""
    absolute = bool(ABS_PATH.match(v))
    p = Path(v.strip('"'))
    if not absolute:
        if base_dir is None or "\n" in v or p.suffix.lower() not in IMAGE_EXT | {".svg"}:
            return v
        p = base_dir / p
    if not p.is_file():
        if absolute:
            msg = f"extra_info: no file at {p}"
            raise AreasError(msg)
        return v
    if p not in cache:
        try:
            cache[p] = _data_uri(p)
        except Exception as e:
            msg = f"extra_info: could not read {p.name}: {e}"
            raise AreasError(msg) from e
    return cache[p]


# ── csv -> areas ───────────────────────────────────────────────────────────


def read_rows(data: bytes) -> list[list[str]]:
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
        msg = "CSV needs a header (GroupName,Level1,...) and at least one row"
        raise AreasError(msg)
    return rows


def build_areas(rows: list[list[str]], layer, base_dir: Path | None = None):
    """-> (names, colors, extras, geometries): one entry per group, geometries in lon/lat."""
    cols = [c for c in LEVEL_COLUMNS if layer is not None and c in layer.columns]
    if not cols:
        msg = "This country has no subdivisions in the local data."
        raise AreasError(msg)
    head = [h.strip().lower().replace(" ", "_") for h in rows[0]]
    extra_i = head.index("extra_info") if "extra_info" in head else None
    level_i = [i for i in range(1, len(head)) if i != extra_i]
    norm = {c: layer[c].fillna("").astype(str).str.strip().str.lower().to_numpy() for c in cols}

    groups: dict[str, int] = {}
    extras: list[str] = []
    entries: list[tuple[int, np.ndarray]] = []  # (group, mask of the units the row selects)
    specific: list[int] = []
    unmatched: list[str] = []
    cache: dict = {}
    for n, row in enumerate(rows[1:], start=2):
        row = [*row, *[""] * (len(head) - len(row))]
        name = row[0].strip()
        vals = [(k, row[i].strip()) for k, i in enumerate(level_i) if row[i].strip()]
        if not name or not vals:
            msg = f"Row {n}: needs a GroupName and at least Level1"
            raise AreasError(msg)
        mask = np.ones(len(layer), dtype=bool)
        for k, v in vals:
            if k >= len(cols):
                msg = f"Row {n}: Level{k + 1} isn't available ({len(cols)} levels for this country)"
                raise AreasError(msg)
            mask &= norm[cols[k]] == v.lower()
        if not mask.any():
            unmatched.append(f"row {n} ({name}: {', '.join(v for _, v in vals)})")
            continue
        g = groups.setdefault(name, len(groups))
        if g == len(extras):
            extras.append("")
        if not extras[g] and extra_i is not None:
            extras[g] = _extra(row[extra_i], base_dir, cache)
        entries.append((g, mask))
        specific.append(len(vals))
    if unmatched:
        more = f" and {len(unmatched) - 5} more" if len(unmatched) > 5 else ""
        msg = "No subdivision matches " + "; ".join(unmatched[:5]) + more
        raise AreasError(msg)

    owner = np.full(len(layer), -1)
    for k in sorted(
        range(len(entries)), key=lambda k: specific[k]
    ):  # specific rows last = they win
        owner[entries[k][1]] = entries[k][0]
    units = shapely.make_valid(layer.geometry.to_numpy())
    names, keep_extra, geoms = [], [], []
    for name, g in groups.items():
        sel = units[owner == g]
        if not len(sel):
            continue
        union = shapely.union_all(sel)
        polys = [p for p in shapely.get_parts(union) if p.geom_type in {"Polygon", "MultiPolygon"}]
        if polys:
            names.append(name)
            keep_extra.append(extras[g])
            geoms.append(shapely.union_all(polys))
    if not geoms:
        msg = "No areas could be built from that CSV"
        raise AreasError(msg)
    geoms = list(
        simplify_coverage(geoms, tolerance(shapely.total_bounds(np.array(geoms, dtype=object))))
    )
    return names, assign_colors(geoms), keep_extra, geoms
