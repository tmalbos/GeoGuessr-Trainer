"""tools.py — The endpoints' work for the "Draw" region editor: the units payload and the built drawing."""

import json

import shapely

from src.explore.country_clues.edit_mode.draw.canvas import (
    COLORS,
    PALE,
    Item,
    _canvas,
    _layer,
    _units,
)
from src.geo.simplify import _polygonal


def _payload(local, cc: str, level: int) -> dict:
    units = _units(local, cc, level)
    return {
        "levels": len(_layer(local, cc)[1]),
        "colors": COLORS,
        "units": [
            {"id": i, "name": n, "geometry": json.loads(shapely.to_geojson(g))}
            for i, (n, g) in enumerate(units)
        ],
    }


def _build(local, cc: str, items: list[Item], colored: bool = True) -> dict:
    canvas = _canvas(local, cc, items)
    shapes, extras, looks, slots = canvas.shapes(), canvas.extras, canvas.looks, canvas.slots
    names, geoms = [], []
    for n, g in shapes.items():
        g = _polygonal(g)
        if g is not None:
            names.append(n)
            geoms.append(g)
    if colored:
        colors = [COLORS[slots.get(n, 0) % len(COLORS)] for n in names]
    else:
        colors = [PALE] * len(geoms)
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {
                    "name": n,
                    "color": c,
                    "extra": extras.get(n, ""),
                    "extra_blur": looks.get(n, (False, None))[0],
                    "extra_max_height": looks.get(n, (False, None))[1],
                },
                "geometry": json.loads(shapely.to_geojson(g)),
            }
            for n, c, g in zip(names, colors, geoms, strict=True)
        ],
    }
