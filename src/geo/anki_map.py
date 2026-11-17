"""anki_map.py — Paint a clue's regions on a pre-made country SVG (made by src/maps/country_to_svg.py).

The base SVG carries its lon/lat -> pixel transform on the root <svg> (data-lon0, data-cos-lat0, data-scale,
data-off-x, data-off-y), so a region can be projected without rebuilding the map.
"""

import html
import re
import unicodedata

import numpy as np
import shapely

PRECISION = 5
_ATTRS = ("lon0", "cos-lat0", "scale", "off-x", "off-y")
STYLE = "fill:#c8283a;fill-opacity:.85;stroke:#c8283a;stroke-width:1;stroke-linejoin:round"


def read_transform(svg: str) -> tuple[float, ...]:
    root = re.search(r"<svg\b[^>]*>", svg)
    found = [re.search(rf'data-{a}="([^"]+)"', root.group(0)) if root else None for a in _ATTRS]
    if not all(found):
        msg = "The base map has no projection data. Regenerate it with country_to_svg."
        raise ValueError(msg)
    return tuple(float(m.group(1)) for m in found)


def _path_d(geom, t: tuple[float, ...]) -> str:
    lon0, cos_lat0, scale, off_x, off_y = t

    def project(c: np.ndarray) -> np.ndarray:
        return np.column_stack(
            [(c[:, 0] - lon0) * cos_lat0 * scale + off_x, -c[:, 1] * scale + off_y]
        )

    out = []
    for part in shapely.get_parts(shapely.transform(geom, project)):
        if part.geom_type != "Polygon":
            continue
        for ring in (part.exterior, *part.interiors):
            pts = " ".join(f"{x:.{PRECISION}f},{y:.{PRECISION}f}" for x, y in ring.coords)
            out.append(f"M{pts}Z")
    return " ".join(out)


def overlay_regions(svg: str, regions: dict[str, object]) -> str:
    """The base SVG with a <g id="Stains"> holding one <path> per region (name -> lon/lat geometry),
    placed under the Provinces / Country border layers.
    """
    t = read_transform(svg)
    paths = []
    for name, geom in regions.items():
        d = _path_d(geom, t)
        if d:
            fid = re.sub(
                r"[^A-Za-z0-9_.-]",
                "_",
                unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode(),
            )
            paths.append(f'<path id="{fid}" data-name="{html.escape(name)}" d="{d}"/>')
    layer = f'<g id="Stains" style="{STYLE}">' + "".join(paths) + "</g>"
    for marker in ('<g id="Provinces"', '<g id="Country"'):
        i = svg.find(marker)
        if i != -1:
            return svg[:i] + layer + svg[i:]
    i = svg.rindex("</svg>")
    return svg[:i] + layer + svg[i:]
