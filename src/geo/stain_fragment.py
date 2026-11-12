"""stain_fragment.py — Placement candidates for a screenshot that shows only a FRAGMENT of a country.

A country made of islands is often screenshotted zoomed in on a few of them. The normal coarse search
(stain_extract._candidates) only tries scales where the WHOLE country fits in the image, so it never finds those.
Here the largest island parts (alone, and together with the parts around them) are searched as small
templates at LARGER scales. A hit also fixes the transform of the whole country (px = a*u + tx), so it goes
through the usual refine / polish like any other candidate; those only look at the part inside the image.

Only used when the normal search is not confident and the country has more than one part.
"""

import numpy as np
import shapely

MIN_PARTS = 2  # fewer parts than this: nothing to search for
MAX_ANCHORS = 4  # the largest parts used as the centre of a template
NEIGHBOUR_SPAN = 4.0  # a template also holds the parts within this many anchor sizes of the anchor
PROJECTIONS = ("mercator", "equirect")  # only the likely ones: the search has to stay fast
FLOOR_MARGIN = (
    1.02  # fragment scales start this far above the largest scale that fits the whole country
)
TOP = 2  # candidates handed to the refinement
DUP_SHARE = 0.03  # two candidates closer than this share of the country's size are the same place


def has_islands(outline) -> bool:
    return len(shapely.get_parts(outline)) >= MIN_PARTS


def _groups(parts) -> list[tuple[int, ...]]:
    """Indices of the parts that make each template: every large part alone and with its neighbours."""
    bnd = shapely.bounds(parts)
    cx, cy = (bnd[:, 0] + bnd[:, 2]) / 2, (bnd[:, 1] + bnd[:, 3]) / 2
    seen, out = set(), []
    for i in np.argsort(-shapely.area(parts))[:MAX_ANCHORS]:
        ext = max(bnd[i, 2] - bnd[i, 0], bnd[i, 3] - bnd[i, 1])
        near = np.flatnonzero(
            (np.abs(cx - cx[i]) <= NEIGHBOUR_SPAN * ext / 2)
            & (np.abs(cy - cy[i]) <= NEIGHBOUR_SPAN * ext / 2)
        )
        for g in ((int(i),), tuple(int(k) for k in near)):
            if g not in seen:
                seen.add(g)
                out.append(g)
    return out


def fragment_candidates(e_coarse, light_coarse, f, outline, inner, hint=None) -> list[dict]:
    """Candidates shaped like stain_extract._candidates' (plus name / uv / uvb / inv / fwd), for the FULL
    country, found from its largest parts. outline = the fit outline (lon/lat), inner = inner borders or None.
    """
    from src.geo import stain_extract as se  # late: stain_extract imports this module

    parts = shapely.get_parts(outline)
    groups = _groups(parts)
    hh, ww = e_coarse.shape
    found = []
    for name in PROJECTIONS:
        proj = se._projection(name, outline.bounds)
        if proj is None:
            continue
        fwd, inv = proj
        uv = shapely.transform(outline, fwd)
        ub = uv.bounds
        tw, th = ub[2] - ub[0], ub[3] - ub[1]
        if tw <= 0 or th <= 0:
            continue
        floor = min(ww / tw, hh / th) * 0.999 * FLOOR_MARGIN
        uvb = None if inner is None else shapely.transform(inner, fwd)
        for members in groups:
            g = shapely.transform(shapely.multipolygons(parts[list(members)]), fwd)
            gb = None
            if uvb is not None:
                gb = shapely.clip_by_rect(uvb, *g.bounds)
                if gb.is_empty:
                    gb = None
            found.extend({**c, "name": name, "uv": uv, "uvb": uvb, "inv": inv, "fwd": fwd,
                     "size": c["a"] * max(tw, th)} for c in se._candidates(e_coarse, light_coarse, f, g, gb, hint, frag_floor=floor))  # fmt: skip

    found.sort(key=lambda c: -c["score"])
    keep: list[dict] = []
    for c in found:
        if c["score"] <= 0:
            break
        if any(
            c["name"] == k["name"]
            and 0.9 < c["a"] / k["a"] < 1.1
            and abs(c["tx"] - k["tx"]) < DUP_SHARE * k["size"]
            and abs(c["ty"] - k["ty"]) < DUP_SHARE * k["size"]
            for k in keep
        ):
            continue
        keep.append(c)
        if len(keep) >= TOP:
            break
    return keep
