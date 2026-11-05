"""pinpoint.py — Turn map-pin icons found among the stains into small circles.

A "pinpoint" clue is a map with a pin on it. The stain tool returns the pin as a small teardrop
polygon. A teardrop says nothing about the area, only the pin's TIP is the location, so each pin is
replaced by a circle centred on the tip:

    radius = min(10 km, the radius whose circle covers 2% of the country's area)

A polygon counts as a pin when it is small compared with the country and teardrop shaped
(taller than wide, heavier at the top, point at the bottom). The colour does not matter. It is judged in lon/lat after the stain tool,
so a pin that the tool already clipped hard against the country outline may not be recognised.
"""

import math

import numpy as np
import shapely
from pyproj import Geod

MAX_RADIUS_KM = 10.0
MAX_AREA_SHARE = 0.02  # a circle may cover at most this share of the country
MAX_SIZE_SHARE = 0.03  # a pin is at most this share of the country's longest side
ASPECT = (1.1, 2.4)  # height / width of a teardrop
TOP_SHARE = 0.55  # share of the area in the upper half (the round head)
MIN_SOLIDITY = 0.85  # area / convex hull area: a pin has no dents
TIP_ZONE = 0.15  # the bottom share of the height that must be the narrow point
TIP_WIDTH = 0.45  # the width of that zone is at most this share of the pin's width
TIP_OFFSET = 0.2  # ...and its centre is within this share of the width from the pin's axis
CIRCLE_POINTS = 64

_GEOD = Geod(ellps="WGS84")


def _km(coords: np.ndarray, lat0: float) -> np.ndarray:
    k = math.cos(math.radians(lat0)) or 1e-6
    return np.column_stack([coords[:, 0] * k * 111.32, coords[:, 1] * 110.57])


def _polys(g) -> list:
    if g is None or g.is_empty:
        return []
    return [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]


def area_km2(geom) -> float:
    total = 0.0
    for p in _polys(geom):
        total += abs(_GEOD.geometry_area_perimeter(shapely.Polygon(p.exterior))[0])
        total -= sum(abs(_GEOD.geometry_area_perimeter(shapely.Polygon(r))[0]) for r in p.interiors)
    return total / 1e6


def _extent_km(geom) -> float:
    w, s, e, n = geom.bounds
    return max((e - w) * (math.cos(math.radians((s + n) / 2)) or 1e-6) * 111.32, (n - s) * 110.57)


def pin_tip(poly, extent_km: float) -> tuple[float, float] | None:
    """(lon, lat) of the tip if `poly` looks like a pin, else None."""
    ring = np.asarray(poly.exterior.coords)
    if len(ring) < 6:
        return None
    solid = shapely.Polygon(_km(ring, float(ring[:, 1].mean())))
    if solid.is_empty or not solid.is_valid:
        return None
    x0, y0, x1, y1 = solid.bounds
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0 or max(w, h) > MAX_SIZE_SHARE * extent_km:
        return None
    if not ASPECT[0] <= h / w <= ASPECT[1]:
        return None
    top = shapely.clip_by_rect(solid, x0, y0 + h / 2, x1, y1).area
    if top < TOP_SHARE * solid.area:
        return None
    if solid.area < MIN_SOLIDITY * solid.convex_hull.area:
        return None
    tx0, _, tx1, _ = shapely.clip_by_rect(solid, x0, y0, x1, y0 + TIP_ZONE * h).bounds
    if not (tx1 - tx0 <= TIP_WIDTH * w and abs((tx0 + tx1) / 2 - (x0 + x1) / 2) <= TIP_OFFSET * w):
        return None
    lat_min, lat_max = float(ring[:, 1].min()), float(ring[:, 1].max())
    low = ring[ring[:, 1] <= lat_min + 0.1 * (lat_max - lat_min)]
    return float(low[:, 0].mean()), lat_min


def circle(lon: float, lat: float, radius_km: float):
    az = np.linspace(0, 360, CIRCLE_POINTS, endpoint=False)
    lons, lats, _ = _GEOD.fwd(
        np.full(CIRCLE_POINTS, lon), np.full(CIRCLE_POINTS, lat), az,
        np.full(CIRCLE_POINTS, radius_km * 1000.0),
    )  # fmt: skip
    return shapely.Polygon(np.column_stack([lons, lats]))


def pin_radius_km(country_km2: float) -> float:
    return min(MAX_RADIUS_KM, math.sqrt(MAX_AREA_SHARE * country_km2 / math.pi))


def convert_pins(geom, country) -> tuple[object, int, int]:
    """Replace the pins of a stain by circles. Returns (geometry, pins, other polygons left)."""
    if country is None or country.is_empty:
        return geom, 0, len(_polys(geom))
    extent = _extent_km(country)
    tips, rest = [], []
    for p in _polys(geom):
        tip = pin_tip(p, extent)
        if tip is None:
            rest.append(p)
        else:
            tips.append(tip)
    if not tips:
        return geom, 0, len(rest)
    r = pin_radius_km(area_km2(country))
    out = shapely.union_all([*rest, *(circle(lon, lat, r) for lon, lat in tips)])
    return out, len(tips), len(rest)
