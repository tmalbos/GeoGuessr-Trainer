"""region_stats.py — Numbers about a region geometry, used to pre-fill a clue's ratings in the editor.

Given the singular parts of an uploaded geometry (after simplification) it returns:
    polygons    how many singular polygons there are (0 for lines / points)
    shades      how many different shades the parts have
    components  how many separate pieces remain after merging the parts (touching / almost-touching
                parts become one; the merge tolerance absorbs the tiny gaps between neighbouring stains)
    spread_km   average, over the components, of the mean distance between points of the same component
                (a single point, or a tiny blob, is ~0). Components are sampled along their outline.
"""

import numpy as np
import shapely

MERGE_TOL_DEG = 0.0006  # ~65 m: gaps smaller than this between parts don't separate them
MAX_COMPONENTS = 500  # more than this and the spread is measured on an evenly spaced subset
SAMPLES = 64  # points taken along each component


def _km(pts: np.ndarray) -> np.ndarray:
    lat0 = np.radians(float(pts[:, 1].mean()))
    return np.column_stack([pts[:, 0] * 111.32 * np.cos(lat0), pts[:, 1] * 110.57])


def _mean_pairwise(xy: np.ndarray) -> float:
    n = len(xy)
    if n < 2:
        return 0.0
    d = np.hypot(xy[:, None, 0] - xy[None, :, 0], xy[:, None, 1] - xy[None, :, 1])
    return float(d.sum() / (n * (n - 1)))


def _sample(part) -> np.ndarray:
    kind = part.geom_type
    if kind == "Point":
        return np.array([[part.x, part.y]], float)
    line = part.exterior if kind == "Polygon" else part
    if not line.length:
        return shapely.get_coordinates(part)[:1]
    at = np.linspace(0, line.length, SAMPLES, endpoint=False)
    return shapely.get_coordinates(shapely.line_interpolate_point(line, at))


def region_stats(parts: np.ndarray, shades: np.ndarray, kind: str) -> dict | None:
    if not len(parts):
        return None
    merged = parts if kind == "points" else shapely.buffer(parts, MERGE_TOL_DEG)
    comps = [c for c in shapely.get_parts(shapely.union_all(merged)) if not c.is_empty]
    if not comps:
        return None
    step = max(1, len(comps) // MAX_COMPONENTS)
    spreads = [_mean_pairwise(_km(_sample(c))) for c in comps[::step]]
    return {
        "polygons": len(parts) if kind == "polygons" else 0,
        "shades": len(np.unique(shades)) if len(shades) else 0,
        "components": len(comps),
        "spread_km": round(float(np.mean(spreads)), 3),
    }
