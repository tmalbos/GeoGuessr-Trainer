"""country_projection.py — Map projection per country (a Settings rule), used by every regional clue of that country.

Rules live in data/local_settings.json as `country_projections: {CC: kind}`. A country without a rule uses "flat".
The pre-drawn overlay SVG of a regional clue bakes the projection in, so saving a changed rule redraws the overlays
of that country once (`rerender`); nothing else is converted.
"""

import logging

import numpy as np
import shapely

from src.explore.country_clues.clues import (
    _LOCK,
    CLUES_DIR,
    GEOM_KINDS,
    _load,
    _overlay_name,
    _save,
    _write_overlay,
)
from src.explore.country_clues.map_projection import KINDS, make_projection
from src.explore.country_clues.regional_map_overlay import _render_overlay
from src.explore.country_clues.regional_stain_areas import _outline
from src.shared import prefs

logger = logging.getLogger("uvicorn.error")

DEFAULT = "flat"
MAP_W = 1000  # width of the projected map; keep equal to W / PAD in frontend projections.js
MAP_PAD = 6
MAX_ZOOM = 80


def clean(rules: dict) -> dict[str, str]:
    """Only valid 2-letter codes with a known, non-default projection."""
    out: dict[str, str] = {}
    for cc, kind in rules.items():
        code = str(cc).strip().upper()
        if len(code) == 2 and code.isalpha() and kind in KINDS and kind != DEFAULT:
            out[code] = kind
    return out


def load() -> dict[str, str]:
    raw = prefs.load().get("country_projections")
    return clean(raw) if isinstance(raw, dict) else {}


def projection_for(cc: str) -> str:
    return load().get(cc.upper(), DEFAULT)


def _frame(kind: str, bbox, pts):
    """(height, to_map, to_lonlat) of a country's map in `kind`: the same fit projections.js makeProjection does."""
    mp = make_projection(kind, bbox, pts)
    umin, vmin, umax, vmax = mp.extent
    w = max(umax - umin, 1e-6)
    h = max(vmax - vmin, 1e-6)
    height = round(min(1400, max(200, MAP_W * h / w)))
    s = min((MAP_W - 2 * MAP_PAD) / w, (height - 2 * MAP_PAD) / h)
    ox = (MAP_W - w * s) / 2
    oy = (height - h * s) / 2

    def to_map(lonlat):
        uv = mp.fwd(lonlat)
        return np.column_stack([ox + (uv[:, 0] - umin) * s, oy + (vmax - uv[:, 1]) * s])

    def to_lonlat(xy):
        return mp.inv(np.column_stack([umin + (xy[:, 0] - ox) / s, vmax - (xy[:, 1] - oy) / s]))

    return height, to_map, to_lonlat


def convert_view(view: dict, old: str, new: str, outline) -> dict | None:
    """A locked view ({k, kh?, fx, fy}: a window of the map, as fractions of it) moved to another projection.

    The window's border is taken back to lon/lat with the old projection and forward with the new one; the new
    window is the box around it. Not exact (a box rarely stays a box), but it frames the same part of the country.
    None when it can't be worked out (the caller then drops the view).
    """
    if outline is None or outline.is_empty:
        return None
    try:
        bbox = tuple(float(v) for v in shapely.bounds(outline))
        pts = shapely.get_coordinates(outline)
        h0, _, to_lonlat = _frame(old, bbox, pts)
        h1, to_map, _ = _frame(new, bbox, pts)
        k = float(view["k"])
        vw = MAP_W / k
        vh = h0 / float(view.get("kh") or k)
        x = min(max(float(view["fx"]) * MAP_W - vw / 2, 0), MAP_W - vw)
        y = min(max(float(view["fy"]) * h0 - vh / 2, 0), h0 - vh)
        t = np.linspace(0.0, 1.0, 11)
        edge = np.concatenate([
            np.column_stack([x + vw * t, np.full_like(t, y)]),
            np.column_stack([x + vw * t, np.full_like(t, y + vh)]),
            np.column_stack([np.full_like(t, x), y + vh * t]),
            np.column_stack([np.full_like(t, x + vw), y + vh * t]),
        ])  # fmt: skip
        moved = to_map(to_lonlat(edge))
        if not np.isfinite(moved).all():
            return None
        x0, y0 = moved.min(axis=0)
        x1, y1 = moved.max(axis=0)
        nw = min(max(x1 - x0, MAP_W / MAX_ZOOM), MAP_W)
        nh = min(max(y1 - y0, h1 / MAX_ZOOM), h1)
        cx = min(max((x0 + x1) / 2, nw / 2), MAP_W - nw / 2)
        cy = min(max((y0 + y1) / 2, nh / 2), h1 - nh / 2)
        out = {
            "k": round(float(MAP_W / nw), 3),
            "fx": round(float(cx / MAP_W), 4),
            "fy": round(float(cy / h1), 4),
        }  # plain floats: numpy ones can't go into the yaml
        kh = round(float(h1 / nh), 3)
        if abs(kh - out["k"]) > 0.01:
            out["kh"] = kh
        return out
    except Exception:
        logger.exception("Could not convert a locked view from %s to %s", old, new)
        return None


def rerender(codes: list[str], local, old: dict[str, str]) -> int:
    """Redraw the overlays of the regional clues of these countries with their current rule.

    Also moves each regional clue's locked `view` (stored as fractions of the projected map) to the new projection,
    see convert_view. `old` = the rules before the change. Returns how many overlays were redrawn.
    """
    redrawn = 0
    for cc in codes:
        folder = CLUES_DIR / cc
        kind = projection_for(cc)
        before = old.get(cc, DEFAULT)
        outline = _outline(local, cc)
        with _LOCK:
            items = _load(cc)
            if not items:
                continue
            for clue in items:
                if clue.get("scope") != "reg":
                    continue
                if clue.get("view"):
                    moved = convert_view(clue["view"], before, kind, outline)
                    if moved:
                        clue["view"] = moved
                    else:
                        clue.pop("view")
                geo = next((k for k in GEOM_KINDS if clue.get(k)), None)
                if not geo:
                    continue
                svg = _render_overlay(cc, folder / clue[geo], local, kind)
                if not svg:
                    logger.warning("Overlay of %s/%s was not redrawn", cc, clue.get("id"))
                    continue
                name = _overlay_name(clue[geo])
                _write_overlay(folder, name, svg)
                clue["overlay"] = name
                redrawn += 1
            _save(cc, items)
    return redrawn
