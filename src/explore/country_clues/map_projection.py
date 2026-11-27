"""map_projection.py — The map projections a regional clue can be drawn in.

A clue stores its choice (`projection`); the editor, the card and the pre-drawn overlay all use it.
The formulas repeat the ones in frontend/src/pages/Explore/clues/projections.js: keep them in step.

  flat      plain map, longitudes squeezed by the cosine of the middle latitude (the default)
  mercator  like web maps (stretches towards the poles)
  conic     Albers equal-area cone fitted to the country's latitudes (wide countries far from the equator)

A projection takes lon/lat arrays of shape (N, 2) and gives (u, v) arrays, v pointing north.
"""

import math

import numpy as np

KINDS = ("flat", "mercator", "conic")
MAX_LAT = 85.0511
EDGE_STEPS = 32
RAD = math.pi / 180


def _standard(bb):
    k = math.cos(math.radians((bb[1] + bb[3]) / 2)) or 1.0

    def fwd(c):
        return np.column_stack([c[:, 0] * k, c[:, 1]])

    def inv(c):
        return np.column_stack([c[:, 0] / k, c[:, 1]])

    return fwd, inv


def _mercator(_bb):
    def fwd(c):
        lat = np.clip(c[:, 1], -MAX_LAT, MAX_LAT)
        return np.column_stack([c[:, 0], np.log(np.tan(np.pi / 4 + lat * RAD / 2)) / RAD])

    def inv(c):
        return np.column_stack([c[:, 0], (2 * np.arctan(np.exp(c[:, 1] * RAD)) - np.pi / 2) / RAD])

    return fwd, inv


def _conic(bb):
    y0 = min(max(bb[1], -89.0), 89.0)
    y1 = min(max(bb[3], -89.0), 89.0)
    phi1 = (y0 + (y1 - y0) / 6) * RAD
    phi2 = (y1 - (y1 - y0) / 6) * RAD
    phi0 = ((y0 + y1) / 2) * RAD
    lam0 = (bb[0] + bb[2]) / 2
    n = (math.sin(phi1) + math.sin(phi2)) / 2
    if abs(n) < 0.01:  # around the equator a cone is just a cylinder
        return _standard(bb)
    big_c = math.cos(phi1) ** 2 + 2 * n * math.sin(phi1)
    rho0 = math.sqrt(max(big_c - 2 * n * math.sin(phi0), 0)) / n
    sg = math.copysign(1.0, n)

    def fwd(c):
        r = np.sqrt(np.maximum(big_c - 2 * n * np.sin(c[:, 1] * RAD), 0)) / n
        t = n * (c[:, 0] - lam0) * RAD
        return np.column_stack([r * np.sin(t), rho0 - r * np.cos(t)])

    def inv(c):
        dy = rho0 - c[:, 1]
        t = np.arctan2(sg * c[:, 0], sg * dy)
        r = np.hypot(c[:, 0], dy)
        sin = np.clip((big_c - (r * n) ** 2) / (2 * n), -1, 1)
        return np.column_stack([lam0 + t / n / RAD, np.degrees(np.arcsin(sin))])

    return fwd, inv


class Projection:
    """fwd / inv on (N, 2) arrays, and `extent` = (umin, vmin, umax, vmax) once projected.

    `pts` = the lon/lat points that will be drawn: the extent is then exact. Without them the edges of `bbox` are used,
    which in a conic is much bigger than the country (its corners swing out into empty space).
    """

    def __init__(self, kind: str, bbox, pts=None) -> None:
        build = {"mercator": _mercator, "conic": _conic}.get(kind, _standard)
        self.fwd, self.inv = build(bbox)
        x0, y0, x1, y1 = bbox
        t = np.linspace(0.0, 1.0, EDGE_STEPS + 1)
        lon = x0 + (x1 - x0) * t
        lat = y0 + (y1 - y0) * t
        edge = np.concatenate(
            [
                np.column_stack([lon, np.full_like(lon, y0)]),
                np.column_stack([lon, np.full_like(lon, y1)]),
                np.column_stack([np.full_like(lat, x0), lat]),
                np.column_stack([np.full_like(lat, x1), lat]),
            ]
        )
        uv = self.fwd(np.asarray(pts, dtype=float)[:, :2] if pts is not None and len(pts) else edge)
        self.extent = (
            float(uv[:, 0].min()),
            float(uv[:, 1].min()),
            float(uv[:, 0].max()),
            float(uv[:, 1].max()),
        )


def make_projection(kind: str, bbox, pts=None) -> Projection:
    return Projection(kind, bbox, pts)
