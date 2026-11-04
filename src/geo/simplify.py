"""simplify.py — One simplification for a country's whole map (outline + first-level borders).

All first-level regions are simplified TOGETHER as a coverage (shapely.coverage_simplify), so a border shared by
two regions stays identical on both sides: no gaps, no overlaps. The outline and the borders are then derived
from that single result, so they can never disagree.

The tolerance is not a fixed number of degrees. It is the size of one pixel when the country's bounding box is
fitted into RESOLUTION (same idea as mapshaper's `-simplify resolution=1600x800`): a small country keeps fine
detail, a big one gets coarser detail, and both look the same at that resolution.
"""

import logging
from collections.abc import Callable

import numpy as np
import shapely

logger = logging.getLogger("uvicorn.error")

RESOLUTION = (1600, 800)


def tolerance(bounds, resolution: tuple[int, int] = RESOLUTION) -> float:
    """Size of one pixel (degrees) when `bounds` = (west, south, east, north) is fitted into `resolution`."""
    west, south, east, north = (float(v) for v in bounds)
    width, height = resolution
    return max((east - west) / width, (north - south) / height, 1e-9)


def _arr(geoms) -> np.ndarray:
    out = np.empty(len(geoms), dtype=object)
    out[:] = list(geoms)
    return out


def _polygonal(g):
    """Only the polygons of a geometry, as one geometry (None when there are none)."""
    parts = []
    stack = [g]
    while stack:
        x = stack.pop()
        if x is None or x.is_empty:
            continue
        if x.geom_type == "Polygon":
            parts.append(x)
        elif hasattr(x, "geoms"):
            stack.extend(x.geoms)
    return shapely.union_all(parts) if parts else None


def simplify_coverage(geoms, tol: float) -> np.ndarray:
    """Simplify polygons together so shared borders stay identical. A feature that would come out empty or
    invalid keeps its original shape; without coverage_simplify (old shapely) nothing is simplified.
    """
    arr = _arr(geoms)
    if not len(arr) or tol <= 0:
        return arr
    try:
        out = shapely.coverage_simplify(arr, tol)
    except Exception:
        logger.warning(
            "coverage_simplify failed (shapely >= 2.1 needed); map left unsimplified", exc_info=True
        )
        return arr
    bad = shapely.is_empty(out) | ~shapely.is_valid(out)
    return np.where(bad, arr, out)


def simplified_map(
    layer, fallback: Callable[[], object] | None = None
) -> tuple[object | None, object | None]:
    """(outline, borders) of a country, simplified together.

    layer: the country's admin GeoDataFrame (needs a `state` column) or None.
    fallback: called only when there is no usable layer; returns the full outline, which is then simplified
              alone (there are no borders to keep in step with it).
    """
    groups = []
    if layer is not None and len(layer) and "state" in layer.columns:
        states = layer["state"].fillna("").astype(str)
        for _, grp in layer.groupby(states):
            g = _polygonal(shapely.union_all(shapely.make_valid(grp.geometry.to_numpy())))
            if g is not None:
                groups.append(g)
    if groups:
        simp = simplify_coverage(groups, tolerance(shapely.total_bounds(_arr(groups))))
        return shapely.union_all(simp), shapely.union_all(shapely.boundary(simp))

    outline = fallback() if fallback is not None else None
    if outline is None or outline.is_empty:
        return None, None
    g = _polygonal(shapely.make_valid(outline))
    if g is None:
        return outline, None
    return shapely.simplify(g, tolerance(g.bounds), preserve_topology=True), None
