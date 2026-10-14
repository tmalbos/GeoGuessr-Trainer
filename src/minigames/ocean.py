"""ocean.py — rebuild the sea inside a square window from OSM coastline lines.

OSM stores no ocean polygon, only coastline ways drawn with LAND ON THE LEFT and SEA ON THE RIGHT. Inside a square
window [-h, h]^2 (local metres, y up) that is enough:
  1. join coastline ways that meet end to end into longer chains,
  2. clip every chain to the window; each piece then enters and leaves through the window border,
  3. a sea polygon follows a piece, then walks CLOCKWISE along the border (sea is on the right, so the border is
     walked with the sea on its right too) until it meets the next piece, and so on until it closes.
A window with no coastline gets no sea. Ends that stop inside the window (the extract was cut there) are extended
straight to the border. Closed rings: counter-clockwise = island (land), clockwise = water inside.
"""

from __future__ import annotations

import numpy as np


def chain_lines(lines: list[np.ndarray]) -> list[np.ndarray]:
    """Join lines whose last point equals another's first point (same OSM node -> identical coordinates)."""
    by_start: dict[tuple[float, float], list[int]] = {}
    ends = set()
    for i, a in enumerate(lines):
        by_start.setdefault((float(a[0, 0]), float(a[0, 1])), []).append(i)
        ends.add((float(a[-1, 0]), float(a[-1, 1])))
    heads = [i for i, a in enumerate(lines) if (float(a[0, 0]), float(a[0, 1])) not in ends]
    used = [False] * len(lines)
    chains: list[np.ndarray] = []
    for i in heads + list(
        range(len(lines))
    ):  # open chains first, then whatever is left (closed loops)
        if used[i]:
            continue
        used[i] = True
        parts = [lines[i]]
        while True:
            last = parts[-1][-1]
            nxt = next(
                (j for j in by_start.get((float(last[0]), float(last[1])), []) if not used[j]), None
            )
            if nxt is None:
                break
            used[nxt] = True
            parts.append(lines[nxt][1:])
        chains.append(np.concatenate(parts))
    return chains


def _on_border(p: np.ndarray, h: float) -> bool:
    return abs(max(abs(p[0]), abs(p[1])) - h) < 1e-6 * max(1.0, h)


def _to_border(p: np.ndarray, d: np.ndarray, h: float) -> np.ndarray:
    """First point where the ray p + t*d (p inside the window) meets the border."""
    t = np.inf
    for k in (0, 1):
        if d[k] > 1e-12:
            t = min(t, (h - p[k]) / d[k])
        elif d[k] < -1e-12:
            t = min(t, (-h - p[k]) / d[k])
    return p + d * t


def _clip(a: np.ndarray, h: float) -> list[np.ndarray]:
    """Clip a polyline to [-h, h]^2 (Liang-Barsky). Pieces stopping inside the window are extended to the border."""
    n = len(a) - 1
    if n < 1:
        return []
    p0 = a[:-1]
    d = a[1:] - p0
    t0, t1, ok = np.zeros(n), np.ones(n), np.ones(n, dtype=bool)
    for p, q in (
        (-d[:, 0], p0[:, 0] + h),
        (d[:, 0], h - p0[:, 0]),
        (-d[:, 1], p0[:, 1] + h),
        (d[:, 1], h - p0[:, 1]),
    ):
        par = p == 0
        ok &= ~(par & (q < 0))
        with np.errstate(divide="ignore", invalid="ignore"):
            r = q / p
        t0 = np.where((p < 0) & ~par, np.maximum(t0, r), t0)
        t1 = np.where((p > 0) & ~par, np.minimum(t1, r), t1)
    ok &= t0 < t1

    raw: list[list[np.ndarray]] = []
    cur: list[np.ndarray] | None = None
    last = -2
    for i in np.flatnonzero(ok):
        s, e = p0[i] + t0[i] * d[i], p0[i] + t1[i] * d[i]
        if cur is not None and i == last + 1 and t0[i] == 0.0:
            cur.append(e)
        else:
            if cur is not None:
                raw.append(cur)
            cur = [s, e]
        last = i
        if t1[i] < 1.0:  # left the window
            raw.append(cur)
            cur = None
    if cur is not None:
        raw.append(cur)

    out = []
    for pts in raw:
        arr = np.array(pts)
        if len(arr) < 2:
            continue
        if not _on_border(arr[0], h):  # the line starts inside the window: extend backwards
            v = arr[0] - arr[1]
            if np.hypot(*v) > 1e-9:
                arr = np.vstack([_to_border(arr[0], v / np.hypot(*v), h), arr])
        if not _on_border(arr[-1], h):
            v = arr[-1] - arr[-2]
            if np.hypot(*v) > 1e-9:
                arr = np.vstack([arr, _to_border(arr[-1], v / np.hypot(*v), h)])
        if len(arr) >= 2:
            out.append(arr)
    return out


def _perimeter(x: float, y: float, h: float) -> float:
    """Position along the border, clockwise from the bottom-left corner (y up): left edge, top, right, bottom."""
    if abs(abs(x) - h) <= abs(abs(y) - h):
        s = (y + h) if x < 0 else (4 * h + (h - y))
    else:
        s = (2 * h + (x + h)) if y > 0 else (6 * h + (h - x))
    return s % (8 * h)


def _area(a: np.ndarray) -> float:
    x, y = a[:, 0], a[:, 1]
    return 0.5 * float(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))


def sea_rings(chains: list[np.ndarray], h: float) -> tuple[list[np.ndarray], list[np.ndarray]]:
    """(sea polygons, land loops) for the window [-h, h]^2. Coordinates are local metres."""
    big = 8 * h
    corners = [(0.0, (-h, -h)), (2 * h, (-h, h)), (4 * h, (h, h)), (6 * h, (h, -h))]
    sea: list[np.ndarray] = []
    land: list[np.ndarray] = []
    pieces: list[np.ndarray] = []
    for a in chains:
        if len(a) < 2:
            continue
        closed = bool(np.all(a[0] == a[-1]))
        if closed and len(a) >= 4 and np.abs(a).max() <= h:  # a whole ring inside the window
            (land if _area(a) > 0 else sea).append(a)
            continue
        if closed:  # start the ring at a vertex outside the window, so its first and last piece are not cut in two
            out_i = int(np.argmax(np.abs(a).max(axis=1) > h))
            a = np.vstack([a[out_i:-1], a[: out_i + 1]])
        pieces.extend(_clip(a, h))

    m = len(pieces)
    starts = [_perimeter(p[0, 0], p[0, 1], h) for p in pieces]
    ends = [_perimeter(p[-1, 0], p[-1, 1], h) for p in pieces]
    seen = [False] * m
    for first in range(m):
        if seen[first]:
            continue
        ring: list[np.ndarray] = []
        i = first
        while not seen[i]:
            seen[i] = True
            ring.append(pieces[i])
            e = ends[i]
            j = min(range(m), key=lambda k: (starts[k] - e) % big)  # next entry, walking clockwise
            gap = (starts[j] - e) % big
            between = sorted(((c - e) % big, pt) for c, pt in corners if 0 < (c - e) % big < gap)
            ring.extend(np.array([pt]) for _d, pt in between)
            i = j
        sea.append(np.concatenate(ring))
    return sea, land
