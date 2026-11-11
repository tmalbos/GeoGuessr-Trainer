"""clue_overlay.py — Pre-rendered, transparent map of a regional clue (a standalone SVG).

The clue card used to download the country's outline + borders and the clue's geometry and draw them
in the browser on every view. This module draws the same picture once, when the clue is saved, into a
self-contained SVG with NO background: the card then just lays it over the photo with a plain <img>.

The look mirrors frontend/src/pages/Explore/clues/RegionPreview.jsx (keep them in step):
  land            the country's outline (cream fill, grey stroke)
  borders         first-level borders
  polygons        red, lighter for rarer shades (`shade` column of the geometry file)
  lines / points  red
  pins            a polygon smaller than 0.09% of the country is drawn as a pin whose tip is the
                  polygon's centre (only when 12 or fewer qualify; more means a scattered region)

The outline and borders must arrive already simplified TOGETHER (src/geo/simplify.py); nothing is simplified
here, so shared borders keep matching.

The viewBox is tight around the drawing (a few units of margin), so the card aligns it with CSS.
Strokes use vector-effect="non-scaling-stroke": they stay the same width in screen px at any size.
"""

import math

import numpy as np
import shapely

WIDTH = 1000  # map units across the country's width (same as the live preview)
PAD = 6  # margin around the drawing, map units
MAX_PIN_SHARE = 0.0005
MAX_PINS = 12
PIN_D = "M0 0C-3 -9 -15 -18 -15 -30A15 15 0 1 1 15 -30C15 -18 3 -9 0 0Z"
PIN_HALF_W = 30
PIN_H = 50

LAND = 'fill="#f6f4e4" stroke="#8b98a8" stroke-width="1" stroke-linejoin="round"'
STAIN_FILL = "#c8283a"
STAIN_STROKE = "#c8283a"
POLY = f'fill="{STAIN_FILL}" fill-opacity=".85" stroke="{STAIN_STROKE}" stroke-width="1" stroke-linejoin="round"'
NSS = 'vector-effect="non-scaling-stroke"'


def recolour(svg: str, fill: str) -> str:
    """The overlay with its stains painted in `fill` (the outline and borders keep their colours)."""
    if fill.lower() == STAIN_FILL:
        return svg
    r, g, b = (int(fill[i : i + 2], 16) for i in (1, 3, 5))
    stroke = "#" + "".join(f"{round(v * 0.55):02x}" for v in (r, g, b))
    svg = svg.replace(f'stroke="{STAIN_STROKE}"', f'stroke="{stroke}"')
    return svg.replace(STAIN_FILL, fill)


def shade_factor(shade: float, top: float) -> float:
    """1 for the main tone (shade 0) down to about 0.35 for the lightest (shade `top`)."""
    return 1 - 0.65 * (shade / top) if top > 0 else 1.0


def _walk(g):
    if g is None or g.is_empty:
        return
    if g.geom_type.startswith("Multi") or g.geom_type == "GeometryCollection":
        for p in g.geoms:
            yield from _walk(p)
    else:
        yield g


def _ring(coords, close: bool) -> str:
    return "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in coords) + ("Z" if close else "")


def _shapes(geom) -> tuple[str, str, list[tuple[float, float]]]:
    """(polygon path, line path, points) of a projected geometry."""
    polys: list[str] = []
    lines: list[str] = []
    pts: list[tuple[float, float]] = []
    for g in _walk(geom):
        kind = g.geom_type
        if kind == "Polygon":
            polys.append(_ring(g.exterior.coords, True))
            polys.extend(_ring(r.coords, True) for r in g.interiors)
        elif kind in {"LineString", "LinearRing"}:
            lines.append(_ring(g.coords, False))
        elif kind == "Point":
            pts.append((g.x, g.y))
    return "".join(polys), "".join(lines), pts


def _centre(poly) -> tuple[float, float]:
    x0, y0, x1, y1 = poly.exterior.bounds
    return (x0 + x1) / 2, (y0 + y1) / 2


def _opacity(op: float | None) -> str:
    return "" if op is None else f' fill-opacity="{op:.3f}" stroke-opacity="{op:.3f}"'


def render_overlay(
    outline, borders, layers: list[tuple[np.ndarray, np.ndarray | None]]
) -> str | None:
    """SVG text, or None when there is nothing to draw.

    outline / borders: lon/lat shapely geometries (or None), already simplified together. layers:
    [(geometries, shades)] where `geometries` are the singular parts of the clue's file and `shades` has
    one int per geometry.
    """
    everything = [
        g
        for g in (outline, *(g for geoms, _ in layers for g in geoms))
        if g is not None and not g.is_empty
    ]
    if not everything:
        return None
    x0, y0, x1, y1 = (float(v) for v in shapely.total_bounds(np.array(everything, dtype=object)))
    if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
        return None

    k = math.cos(math.radians((y0 + y1) / 2)) or 1.0
    w = max((x1 - x0) * k, 1e-6)
    h = max(y1 - y0, 1e-6)
    height = round(min(1400, max(200, WIDTH * h / w)))
    s = min((WIDTH - 2 * PAD) / w, (height - 2 * PAD) / h)

    def project(c: np.ndarray) -> np.ndarray:
        return np.column_stack([PAD + (c[:, 0] - x0) * k * s, PAD + (y1 - c[:, 1]) * s])

    def proj(g):
        return shapely.transform(g, project)

    land = _shapes(proj(outline))[0] if outline is not None else ""
    border_d = _shapes(proj(borders))[1] if borders is not None and not borders.is_empty else ""
    total = proj(outline).area if outline is not None else 0.0

    parts: list[str] = []
    if land:
        parts.append(f'<path d="{land}" {LAND} {NSS} fill-rule="evenodd"/>')
    if border_d:
        parts.append(
            f'<path d="{border_d}" fill="none" stroke="#8b98a8" stroke-width=".8" stroke-linejoin="round" {NSS}/>'
        )

    # extent of what is drawn (pins stick out above their tip), for the tight viewBox
    minx = miny = math.inf
    maxx = maxy = -math.inf

    def grow(a: float, b: float, c: float, d: float) -> None:
        nonlocal minx, miny, maxx, maxy
        minx, miny, maxx, maxy = min(minx, a), min(miny, b), max(maxx, c), max(maxy, d)

    for g in (outline, *(g for geoms, _ in layers for g in geoms)):
        if g is None or g.is_empty:
            continue
        a, b, c, d = shapely.bounds(proj(g))
        grow(float(a), float(b), float(c), float(d))

    for geoms, shades in layers:
        sh = np.zeros(len(geoms), dtype=int) if shades is None else np.asarray(shades, dtype=int)
        pg = [proj(g) for g in geoms]
        small = [
            i for i, g in enumerate(pg)
            if g.geom_type == "Polygon" and total > 0 and g.area / total <= MAX_PIN_SHARE
        ]  # fmt: skip
        pins: list[tuple[tuple[float, float], int]] = []
        if 0 < len(small) <= MAX_PINS:
            pins = [(_centre(pg[i]), int(sh[i])) for i in small]
            gone = set(small)
        else:
            gone = set()
        rest = [i for i in range(len(pg)) if i not in gone]
        rest_geoms = [pg[i] for i in rest]
        rest_shades = [int(sh[i]) for i in rest]

        _, lines, pts = (
            _shapes(shapely.GeometryCollection(rest_geoms)) if rest_geoms else ("", "", [])
        )
        if rest_geoms and any(v > 0 for v in rest_shades):
            tiers = sorted(
                set(rest_shades), reverse=True
            )  # lightest first: the main tone ends up on top
            top = max(tiers)
            for v in tiers:
                d = _shapes(
                    shapely.GeometryCollection(
                        [g for g, sv in zip(rest_geoms, rest_shades, strict=True) if sv == v]
                    )
                )[0]
                if d:
                    op = 0.85 * shade_factor(v, top) if v else None
                    # POLY already has a fill-opacity: a second one makes the SVG invalid (broken image).
                    poly = POLY if op is None else POLY.replace(' fill-opacity=".85"', "")
                    parts.append(f'<path d="{d}" {poly}{_opacity(op)} {NSS} fill-rule="evenodd"/>')
        elif rest_geoms:
            d = _shapes(shapely.GeometryCollection(rest_geoms))[0]
            if d:
                parts.append(f'<path d="{d}" {POLY} {NSS} fill-rule="evenodd"/>')
        if lines:
            parts.append(
                f'<path d="{lines}" fill="none" stroke="#c8283a" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" {NSS}/>'
            )
        for x, y in pts:
            parts.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="#c8283a" stroke="#fff" stroke-width="1" {NSS}/>'
            )

        pin_top = max([0, *(v for _, v in pins)])
        for (x, y), v in pins:
            op = 0.85 * shade_factor(v, pin_top) if v else None
            parts.append(
                f'<g transform="translate({x:.1f} {y:.1f})" fill="#c8283a" '
                f'stroke="#c8283a" stroke-width="1"{_opacity(op) or ' fill-opacity=".85"'}>'
                f'<path d="{PIN_D}" {NSS}/><circle cy="-30" r="5.5" fill="#fff" stroke="none"/></g>'
            )
            grow(x - PIN_HALF_W, y - PIN_H, x + PIN_HALF_W, y)

    if not parts or not math.isfinite(minx):
        return None
    minx, miny, maxx, maxy = minx - PAD, miny - PAD, maxx + PAD, maxy + PAD
    # a single point has no size: give it some room
    if maxx - minx < 60:
        cx = (minx + maxx) / 2
        minx, maxx = cx - 30, cx + 30
    if maxy - miny < 60:
        cy = (miny + maxy) / 2
        miny, maxy = cy - 30, cy + 30
    vw, vh = maxx - minx, maxy - miny
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{minx:.1f} {miny:.1f} {vw:.1f} {vh:.1f}" '
        f'width="{vw:.0f}" height="{vh:.0f}">' + "".join(parts) + "</svg>"
    )
