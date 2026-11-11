"""stain_extract.py — Turn a screenshot of a country map with painted areas ("stains") into polygons.

Every colour found inside the map is returned as a polygon, with the base map flagged `base`.
The app already has the country's own map and draws it underneath, so the UI leaves the base
unselected and only the stains are exported by default.

The map is found by REGISTRATION, not by guessing from colours. The country is given, so its
outline and first-level borders are known:

  0. hint          (optional) a rough box of where a map was detected in the image (map_detect.py,
                   run on upload). It is only a PRIOR: placements that cover it and are not far
                   bigger than it are favoured, the others are not forbidden (the box can be too
                   small, shifted or wrong).
  1. _edges        crisp edges of the image, kept only next to LIGHT pixels (a drawn map sits on a
                   light base; trees, roofs and shadows of a photo mostly don't).
  2. _candidates   coarse search: slide the known outline + borders over the edge map at several
                   scales, per projection. The score is "line pixels that land on an edge, above
                   what chance gives" times "how light the inside of the placed outline is"
                   times the hint prior.
  3. _refine       the best few placements are refined with annealed ICP against the edge pixels.
                   (The coarse search runs on a SMALL copy; the refinement runs on the WORK image,
                   which is close to the screenshot's native resolution.)
  3b. _polish      EDGE-ONLY final fit. The placement is refined with a FULL AFFINE (scale x/y,
                   rotation, shear, shift) by maximising how well the known lines sit on image
                   edges, at three scales (coarse to fine):
                     - edges come from Lab colour gradients (no "near light" restriction, so pale
                       maps on pale sky and borders between painted regions both count);
                     - an edge only counts when its DIRECTION matches the direction of the line
                       at that point (trees, rivers and stain borders crossing the line don't vote);
                     - the cost is a blurred, oriented edge field sampled at sub-pixel positions,
                       so a 1-2 px error is visible to the optimiser and a drawn stroke (two edges
                       a pixel apart) is centred on instead of snapped to one side.
  3c. _honest      the displayed match. Per outline / border sample: distance (px) to the nearest
                   edge of matching direction. Reported as the share within MATCH_TOL_PX, the
                   median and p90, next to the SAME numbers for the outline shifted by a few px
                   (the chance level) and the share of nearby image edges that our line explains.
                   The refined candidate with the best chance-corrected match (nudged by the hint
                   prior) is the one kept.
  4. colours       everything below runs on the FULL-resolution image (see _classify). The maps this
                   tool reads use very few flat colours, so:
                     - the pixels inside the placed outline (SCOPE_PX beyond it) are counted by
                       exact colour and ordered from the most common one;
                     - grey / black / white / very light blue pixels are never a stain (text,
                       borders, water, paper): they are tagged "undefined" and are not compared
                       to anything;
                     - the BASE colour is the most common pale and light colour (even when it is
                       not the most common colour overall). It does not count towards max_classes;
                     - walking down the list, a colour further than DELTA E (CIEDE2000) from the
                       base and from the stains already chosen becomes a new stain, until
                       max_classes stains exist. The colours left over take the closest of the
                       chosen ones (base included);
                     - a stain made mostly of small / thin disconnected pieces is anti-aliasing
                       between two other colours, not a stain: all its pixels become undefined;
                     - the small / thin pieces of every stain are the blend between two stains
                       (anti-aliasing): each one joins the closest non-small stain (a tie goes to
                       the darker one), or becomes undefined when none is close;
                     - the remaining stains grow into undefined pixels, darkest first. Each stain
                       has EXPAND_PX points to spend and stepping onto a pixel costs its
                       resistance: 1, or BORDER_RESISTANCE for the grey pixels of the inner
                       borders, so a border slows the growth down;
                     - whatever is still undefined goes to the base.
  5. polygons      each label -> polygon straight from its pixels, so neighbouring stains share the
                   exact same boundary. No per-stain growing: the only changes are an optional
                   gap-free simplification (sub-pixel tolerance) when the polygons form a valid
                   coverage, and a final small boundary smoothing that only rounds the pixel steps.
                   Then lon/lat, clipped to the outline.

The registration is a 2x3 affine in the projected plane: px = A @ (u, v) + t. A is a full 2x2 matrix
(it may rotate / shear), so every consumer uses A and t, never separate scale factors.

The outline used to fit and to clip comes ready-made and already simplified (scripts/convert_geo.py,
at the resolution chosen in Settings); nothing is simplified or dissolved here.
"""

import base64
import io
import json
import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageOps
from shapely.affinity import affine_transform

try:  # much faster nearest-neighbour search when scipy is around
    from scipy.spatial import cKDTree
except ImportError:  # pragma: no cover
    cKDTree = None

try:  # distance to the nearest drawn edge (for the match metric)
    from scipy.ndimage import distance_transform_edt
except ImportError:  # pragma: no cover
    distance_transform_edt = None

try:  # blurred edge fields for the affine polish
    from scipy.ndimage import gaussian_filter
except ImportError:  # pragma: no cover
    gaussian_filter = None

try:  # connected pieces of a stain (the fragment filter)
    from scipy.ndimage import label as nd_label
except ImportError:  # pragma: no cover
    nd_label = None

try:  # optimiser of the affine polish
    from scipy.optimize import minimize
except ImportError:  # pragma: no cover
    minimize = None

logger = logging.getLogger("uvicorn.error")

_POOL = ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 4))  # scipy filters release the GIL

WORK_MAX = 1600  # longest side of the image used to find the map, px (near native for a screenshot)
FULL_MAX = 2400  # longest side of the image the stains are read from, px
MIN_CROP = 100  # smallest crop side (in work px)
EDGE_T = (
    250.0  # gradient magnitude (sum over channels) that counts as an edge (coarse search / ICP)
)
LIGHT_MIN = 170  # a pixel is "light" when its darkest channel is at least this
COARSE_MAX = 300  # longest side of the image used by the coarse search
SCALE_STEP = 1.05  # ratio between two scales of the coarse search
MIN_MAP_FRACTION = 0.2  # smallest map considered, relative to the largest that fits the image
TOP_K = 2  # coarse candidates that get refined
ICP_ITERS = 45
SIMPLIFY_PX = 0  # gap-free simplification tolerance, in full-res px (sub-pixel)
SMOOTH_PX = 0.5  # final boundary smoothing radius, in full-res px (no shape change beyond it)

# Hint prior (the detected map box). All soft: the weakest placement still keeps HINT_FLOOR of its score.
HINT_FLOOR = 0.3  # prior of a placement that does not touch the hint at all
HINT_SIZE_FLOOR = (
    0.5  # extra factor of a placement much bigger than the hint (the hint may be too small)
)
HINT_SIZE_SLACK = 1.5  # a placement whose overlap is >= 1/this of its area gets no size penalty
HINT_FINAL_WEIGHT = 0.15  # how much the prior nudges the final choice among refined candidates

# Match metric: a projected line sample "matches" when a drawn edge of matching direction is this
# close (work px).
MATCH_TOL_PX = 1.5
MATCH_STEP_PX = 0.5  # spacing of the samples along a projected line (old metric, fallback only)
MATCH_LIFT_KEEP = (
    0.6  # only candidates with at least this share of the best lift compete (fallback)
)

# ── Affine polish (edge-only) ──────────────────────────────────────────────
N_BINS = 12  # line directions are bucketed in this many bins over 180 degrees
EDGE_LAB_LO = 3.0  # Lab gradient (central difference) below this is not an edge
EDGE_LAB_HI = 12.0  # ...and from this on it is a full-strength edge (strong ones don't dominate)
EDGE_PRE_SIGMA = 0.7  # light blur before the gradient: JPEG noise is not an edge
ALIGN_POWER = 2  # how sharply an edge must match the line direction (cos(2*angle) ** power)
STRONG_W = 0.5  # edge strength that counts as "an edge is here" in the metrics
ALIGN_COS = 0.5  # cos(2*angle) >= this: edge within 30 degrees of the line direction
POLISH_SIGMA_FRAC = (0.015, 0.0075, 0.0035)  # blur of each polish level, as a share of the map size
POLISH_SIGMA_CAP = (8.0, 4.0, 2.0)  # ...never more than this (px)
POLISH_SIGMA_MIN = 0.9  # ...and never less
POLISH_BORDER_W = 0.5  # inner borders count half as much as the outline
POLISH_REG = 0.02  # tiny pull towards "no change" (keeps clutter from stretching the map)
SAMPLES_PER_DIAG = 700  # line samples: one per this fraction of the map diagonal
BAND_PX = 5.0  # "edges near the line" for the reverse check
CHANCE_SHIFT_FRAC = 0.04  # chance level = line shifted by this share of the map size...
CHANCE_SHIFT_MIN, CHANCE_SHIFT_MAX = 4.0, 12.0  # ...limited to this range (px)

# Colours / labels
DELTA_E = 14.61  # CIEDE2000 distance under which two colours are the same stain (UI slider)
MAX_CLASSES = 4  # stains kept besides the base map (UI slider)
EXPAND_PX = 2  # pixel layers the stains grow into undefined pixels (UI slider)
SCOPE_PX = 0  # px beyond the fitted outline whose pixels are read (constant, not in the UI)
BASE_L_MIN = 85.0  # the base map is at least this light (Lab L)...
BASE_CHROMA_MAX = 30.0  # ...and at most this saturated (Lab chroma)
HOLE_MIN = 10000.0  # holes in a stain smaller than this (px² at work size) are filled
GREY_SPREAD = (
    30  # max(R,G,B) - min(R,G,B) at or under this: grey / black / white, not a stain colour
)
BLACK_MAX = 70  # a colour whose brightest channel is under this is black
LIGHT_BLUE_HUE = (170.0, 260.0)  # degrees
LIGHT_BLUE_SAT_MAX = 0.5  # a blue this pale (low saturation) and this bright is "light blue"
LIGHT_BLUE_VAL_MIN = 0.7
SMALL_PIECE_PX = 6  # a connected piece of a stain with fewer px than this is "small" (full-res px)
FRAGMENT_SHARE = 0.5  # a stain with more than this share of its pixels in small pieces is dropped
SPECK_REASSIGN_PX = (
    200  # a small piece of a stain joins the closest non-small stain at most this far away (px)
)
CLIP_PX = 3  # the stains may reach this many px past the app's own country outline
BORDER_INSET_PX = 4  # work px: grey pixels this close to the country outline are not inner borders
BORDER_WHITE_MIN = 235  # a grey this bright (brightest channel) is paper / white, not a border
MIN_MAP_SIDE_PX = 100  # a placed map must be at least this wide AND this tall (work px)
MIN_MAP_LONG_PX = 300  # ...and its longest side at least this long (work px)
MIN_STAIN_WIDTH_PX = 3  # a stain piece narrower than this (2 px wide or less, before growth) is not a stain: undefined
# Colour tiers, in PRIORITY order: a colour belongs to the FIRST tier it fits. Reorder the rows to change priority.
#   (name, colour, delta_e, only_in_inner, resistance)
#   name           base / water / stain / undefined are special (see _classify). Any other name is a fixed-colour
#                  tier of undefined pixels (borders, text...). A tier called "undefined" catches everything left.
#   colour         "#RRGGBB", or "dynamic" (base: the most common pale colour; water: light blue; stain: found in
#                  the image; undefined: whatever is left)
#   delta_e        CIEDE2000 distance to that colour that still counts as the tier. None = the Delta E slider.
#                  (ignored by dynamic water / undefined)
#   only_in_inner  True: only applies away from the country outline (BORDER_INSET_PX); near it the tier is skipped
#   resistance     cost for a stain to grow over such a pixel. -1 = cannot grow into it. (Stain pixels that were
#                  dropped as too thin use the resistance of "undefined".)
COLOR_TIERS = [
    ("inner_border", "#869696", 14.61, True, 3),
    ("outer_border", "#6D706D", 14.61, False, 1),
    ("water", "dynamic", 0.0, False, 1),
    ("coastline", "#2077A2", 14.61, False, 3),
    ("base", "dynamic", 14.61, False, -1),
    ("stain", "dynamic", None, False, 0),
    ("undefined", "dynamic", 0.0, False, 2),
]


class StainError(Exception):
    """A problem the user can act on (shown as a 422)."""


# ── image helpers ──────────────────────────────────────────────────────────


def load_image(data: bytes) -> tuple[np.ndarray, np.ndarray]:
    """Returns (full, work): the image capped at FULL_MAX, and a smaller copy (WORK_MAX) of it."""
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as e:
        msg = "Could not read that image"
        raise StainError(msg) from e
    img = ImageOps.exif_transpose(img)
    if "A" in img.getbands() or "transparency" in img.info:
        rgba = img.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        bg.alpha_composite(rgba)
        img = bg
    img = img.convert("RGB")
    s = FULL_MAX / max(img.size)
    if s < 1:
        size = (max(1, round(img.width * s)), max(1, round(img.height * s)))
        img = img.resize(size, Image.Resampling.BOX)
    work = img
    s = WORK_MAX / max(img.size)
    if s < 1:
        size = (max(1, round(img.width * s)), max(1, round(img.height * s)))
        work = img.resize(size, Image.Resampling.BOX)
    return np.asarray(img), np.asarray(work)


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    """Square dilation of radius r (separable shifts; the outside of the image counts as empty)."""
    out = m.copy()
    for s in range(1, r + 1):
        out[s:] |= m[:-s]
        out[:-s] |= m[s:]
    rows = out.copy()
    for s in range(1, r + 1):
        out[:, s:] |= rows[:, :-s]
        out[:, :-s] |= rows[:, s:]
    return out


def _erode(m: np.ndarray, r: int) -> np.ndarray:
    """Square erosion of radius r (the outside of the image counts as full, so edges don't erode)."""
    return ~_dilate(~m, r)


def _open(m: np.ndarray, r: int) -> np.ndarray:
    return _dilate(_erode(m, r), r)


def _runs_geom(m: np.ndarray):
    """Boolean mask -> polygon(s) in pixel-edge coordinates (x right, y down), exactly the pixels
    of the mask: no simplification, no smoothing. Horizontal runs are merged vertically first.
    """
    p = np.pad(m, ((0, 0), (1, 1))).astype(np.int8)
    d = np.diff(p, axis=1)
    ys, xs0 = np.nonzero(d == 1)
    _, xs1 = np.nonzero(d == -1)
    if not len(ys):
        return shapely.GeometryCollection()
    order = np.lexsort((ys, xs1, xs0))
    y_s, a_s, b_s = ys[order], xs0[order], xs1[order]
    new = np.ones(len(y_s), bool)
    new[1:] = (a_s[1:] != a_s[:-1]) | (b_s[1:] != b_s[:-1]) | (y_s[1:] != y_s[:-1] + 1)
    starts = np.flatnonzero(new)
    ends = np.append(starts[1:], len(y_s)) - 1
    return shapely.union_all(shapely.box(a_s[starts], y_s[starts], b_s[starts], y_s[ends] + 1))


def _polys(g) -> list:
    if g is None or g.is_empty:
        return []
    return [p for p in shapely.get_parts(g) if p.geom_type == "Polygon" and not p.is_empty]


def _poly_only(g):
    """Only the polygonal part of a geometry as one geometry (empty if there is none)."""
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
    return shapely.union_all(parts) if parts else shapely.GeometryCollection()


def _hex(c) -> str:
    return "#{:02x}{:02x}{:02x}".format(*(int(max(0, min(255, round(v)))) for v in c))


def _lab(rgb: np.ndarray) -> np.ndarray:
    """(N, 3) RGB 0..255 -> (N, 3) CIE Lab (D65)."""
    c = rgb.astype(np.float32) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array(
        [[0.4124, 0.3576, 0.1805], [0.2126, 0.7152, 0.0722], [0.0193, 0.1192, 0.9505]], np.float32
    )
    xyz = (c @ m.T) / np.array([0.95047, 1.0, 1.08883], np.float32)
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.column_stack(
        [116 * f[:, 1] - 16, 500 * (f[:, 0] - f[:, 1]), 200 * (f[:, 1] - f[:, 2])]
    )


def _delta_e(lab1: np.ndarray, lab2: np.ndarray) -> np.ndarray:
    """CIEDE2000 colour difference between Lab colours (arrays broadcast on all but the last axis)."""
    lab1 = np.asarray(lab1, np.float64)
    lab2 = np.asarray(lab2, np.float64)
    l1, a1, b1 = lab1[..., 0], lab1[..., 1], lab1[..., 2]
    l2, a2, b2 = lab2[..., 0], lab2[..., 1], lab2[..., 2]
    c1, c2 = np.hypot(a1, b1), np.hypot(a2, b2)
    cb7 = ((c1 + c2) / 2) ** 7
    g = 0.5 * (1 - np.sqrt(cb7 / (cb7 + 25.0**7)))
    a1p, a2p = (1 + g) * a1, (1 + g) * a2
    c1p, c2p = np.hypot(a1p, b1), np.hypot(a2p, b2)
    h1 = np.degrees(np.arctan2(b1, a1p)) % 360
    h2 = np.degrees(np.arctan2(b2, a2p)) % 360
    zero = c1p * c2p == 0
    dh = h2 - h1
    dh = np.where(dh > 180, dh - 360, np.where(dh < -180, dh + 360, dh))
    dh = np.where(zero, 0.0, dh)
    d_l = l2 - l1
    d_c = c2p - c1p
    d_h = 2 * np.sqrt(c1p * c2p) * np.sin(np.radians(dh / 2))
    lb = (l1 + l2) / 2
    cbp = (c1p + c2p) / 2
    hs = h1 + h2
    hb = np.where(np.abs(h1 - h2) > 180, np.where(hs < 360, (hs + 360) / 2, (hs - 360) / 2), hs / 2)
    hb = np.where(zero, hs, hb)
    t = (
        1
        - 0.17 * np.cos(np.radians(hb - 30))
        + 0.24 * np.cos(np.radians(2 * hb))
        + 0.32 * np.cos(np.radians(3 * hb + 6))
        - 0.20 * np.cos(np.radians(4 * hb - 63))
    )
    d_theta = 30 * np.exp(-(((hb - 275) / 25) ** 2))
    rc = 2 * np.sqrt(cbp**7 / (cbp**7 + 25.0**7))
    sl = 1 + 0.015 * (lb - 50) ** 2 / np.sqrt(20 + (lb - 50) ** 2)
    sc = 1 + 0.045 * cbp
    sh = 1 + 0.015 * cbp * t
    rt = -np.sin(np.radians(2 * d_theta)) * rc
    x_c, x_h = d_c / sc, d_h / sh
    return np.sqrt((d_l / sl) ** 2 + x_c**2 + x_h**2 + rt * x_c * x_h)


# ── projections ────────────────────────────────────────────────────────────

PROJECTIONS = ("equirect", "plate", "mercator", "lcc", "albers")


def _projection(name: str, bounds):
    """(forward, inverse) of a projection fitted to the country's lon/lat bounds, or None when it makes
    no sense for that country (conics for small / equatorial countries). forward: (N, 2) lon/lat ->
    plane, inverse: the other way.
    """
    west, south, east, north = bounds
    lon0, lat0 = (west + east) / 2, (south + north) / 2

    if name == "plate":
        return (lambda xy: np.array(xy, float)), (lambda uv: np.array(uv, float))
    if name == "equirect":
        k = math.cos(math.radians(lat0)) or 1e-6
        return (
            lambda xy: np.column_stack([xy[:, 0] * k, xy[:, 1]]),
            lambda uv: np.column_stack([uv[:, 0] / k, uv[:, 1]]),
        )
    if name == "mercator":
        return (
            lambda xy: np.column_stack(
                [
                    np.radians(xy[:, 0]),
                    np.log(np.tan(np.pi / 4 + np.radians(np.clip(xy[:, 1], -85, 85)) / 2)),
                ]
            ),
            lambda uv: np.column_stack(
                [np.degrees(uv[:, 0]), np.degrees(2 * np.arctan(np.exp(uv[:, 1])) - np.pi / 2)]
            ),
        )
    if name not in {"lcc", "albers"}:
        return None

    # Conics. Southern countries are mirrored so the maths is always the northern one.
    if north - south < 4 or south * north <= 0 or abs(lat0) > 75:
        return None
    sgn = 1 if lat0 >= 0 else -1
    lo, hi = sorted((sgn * south, sgn * north))
    p1 = math.radians(lo + (hi - lo) / 6)
    p2 = math.radians(hi - (hi - lo) / 6)
    p0 = math.radians((lo + hi) / 2)

    if name == "lcc":
        t = lambda p: np.tan(np.pi / 4 + p / 2)  # noqa: E731
        n = math.log(math.cos(p1) / math.cos(p2)) / math.log(t(p2) / t(p1))
        big_f = math.cos(p1) * t(p1) ** n / n
        rho0 = big_f / t(p0) ** n

        def rho(phi):
            return big_f / t(np.clip(phi, -1.5, 1.5)) ** n

        def phi_of(r):
            return 2 * np.arctan((big_f / np.maximum(r, 1e-9)) ** (1 / n)) - np.pi / 2
    else:
        n = (math.sin(p1) + math.sin(p2)) / 2
        c = math.cos(p1) ** 2 + 2 * n * math.sin(p1)
        rho0 = math.sqrt(c - 2 * n * math.sin(p0)) / n

        def rho(phi):
            return np.sqrt(np.maximum(c - 2 * n * np.sin(phi), 0)) / n

        def phi_of(r):
            return np.arcsin(np.clip((c - (r * n) ** 2) / (2 * n), -1, 1))

    def fwd(xy):
        lam = np.radians(xy[:, 0] - lon0)
        r = rho(sgn * np.radians(xy[:, 1]))
        return np.column_stack([r * np.sin(n * lam), sgn * (rho0 - r * np.cos(n * lam))])

    def inv(uv):
        dy = rho0 - sgn * uv[:, 1]
        r = np.hypot(uv[:, 0], dy)
        lam = np.arctan2(uv[:, 0], dy) / n
        out = np.column_stack([lon0 + np.degrees(lam), sgn * np.degrees(phi_of(r))])
        return np.nan_to_num(out)

    return fwd, inv


# ── edges ──────────────────────────────────────────────────────────────────


def _grad(a: np.ndarray) -> np.ndarray:
    """Central-difference gradient magnitude over all channels."""
    f = a.astype(np.float32)
    gx = np.zeros_like(f)
    gy = np.zeros_like(f)
    gx[:, 1:-1] = f[:, 2:] - f[:, :-2]
    gy[1:-1] = f[2:] - f[:-2]
    return np.sqrt((gx**2 + gy**2).sum(2))


def _edges(a: np.ndarray) -> np.ndarray:
    """Boolean edge map."""
    return _grad(a) > EDGE_T


def _fast_len(n: int) -> int:
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


# ── hint prior ─────────────────────────────────────────────────────────────


def _hint_prior(px0, py0, pw, ph, hint):
    """How well a placed map box (px0, py0, pw, ph) agrees with the hint box (x0, y0, x1, y1), 0..1.

    The hint is a rough guess of where the map is, often too small (parts of the map that are not
    the main fill colour are missed) and sometimes shifted, so this is a soft preference only:
      coverage  share of the hint that the placed box covers (the map should include what was seen)
      size      a placed box much bigger than its overlap with the hint is slightly less likely
    Works on scalars or broadcastable arrays. With no hint it is 1 everywhere.
    """
    if hint is None:
        return 1.0
    hx0, hy0, hx1, hy1 = hint
    area = max((hx1 - hx0) * (hy1 - hy0), 1e-9)
    ox = np.maximum(0.0, np.minimum(px0 + pw, hx1) - np.maximum(px0, hx0))
    oy = np.maximum(0.0, np.minimum(py0 + ph, hy1) - np.maximum(py0, hy0))
    inter = ox * oy
    cover = np.clip(inter / area, 0.0, 1.0)
    inside = np.clip(inter / np.maximum(pw * ph, 1e-9) * HINT_SIZE_SLACK, 0.0, 1.0)
    return (HINT_FLOOR + (1 - HINT_FLOOR) * cover) * (
        HINT_SIZE_FLOOR + (1 - HINT_SIZE_FLOOR) * inside
    )


def _placed_box(uv, amat, tvec) -> tuple[float, float, float, float]:
    """Axis-aligned box (x, y, width, height) of a plane geometry placed by px = A @ uv + t."""
    b = uv.bounds
    corners = np.array([[b[0], b[1]], [b[0], b[3]], [b[2], b[1]], [b[2], b[3]]], float)
    xy = corners @ np.asarray(amat, float).T + np.asarray(tvec, float)
    lo, hi = xy.min(0), xy.max(0)
    return float(lo[0]), float(lo[1]), float(hi[0] - lo[0]), float(hi[1] - lo[1])


def _box_ok(w: float, h: float) -> bool:
    """A placed map must be at least MIN_MAP_SIDE_PX wide and tall, and its longest side at least MIN_MAP_LONG_PX."""
    return min(w, h) >= MIN_MAP_SIDE_PX and max(w, h) >= MIN_MAP_LONG_PX


# ── match metric (old, distance to ANY edge; only the fallback without scipy) ──


def _line_match(geom, dt: np.ndarray | None, tol: float = MATCH_TOL_PX) -> dict | None:
    """How much of a projected line (placed in crop-local px) lies on a drawn edge.

    dt = distance (px) from every pixel to the nearest edge pixel. The line is sampled every
    MATCH_STEP_PX; samples outside the crop are ignored.
    Returns {precision, median, p90, tol} or None when it can't be measured.
    """
    if dt is None or geom is None or geom.is_empty:
        return None
    pts = shapely.get_coordinates(shapely.segmentize(geom, MATCH_STEP_PX))
    if not len(pts):
        return None
    hh, ww = dt.shape
    xs = np.rint(pts[:, 0] - 0.5).astype(int)  # pixel centres sit at +0.5
    ys = np.rint(pts[:, 1] - 0.5).astype(int)
    ok = (xs >= 0) & (xs < ww) & (ys >= 0) & (ys < hh)
    if ok.sum() < 10:
        return None
    d = dt[ys[ok], xs[ok]]
    return {
        "precision": float(np.mean(d <= tol)),
        "median": float(np.median(d)),
        "p90": float(np.quantile(d, 0.9)),
        "tol": tol,
    }


def _match_key(r: dict) -> tuple[float, float]:
    """Sort key of a refined candidate (fallback): the displayed match first (nudged by the hint
    prior, which only breaks near-ties), the chance-corrected lift second.
    """
    prior = r.get("prior", 1.0)
    nudge = 1 - HINT_FINAL_WEIGHT + HINT_FINAL_WEIGHT * prior
    m_o, m_b = r.get("m_o"), r.get("m_b")
    if m_o is None:
        return (0.0, r["lift"] * nudge)
    p = m_o["precision"]
    if m_b is not None:
        p = 0.5 * (p + m_b["precision"])
    return (p * nudge, r["lift"])


# ── coarse search ──────────────────────────────────────────────────────────


def _candidates(
    e_coarse: np.ndarray, light_coarse: np.ndarray, f: int, uv, uvb, hint=None
) -> list[dict]:
    """Best placement of the known lines at every scale. Returns [{score, a, tx, ty}] in FULL-res
    px for x = a*u + tx, y = -a*v + ty.

    score = (line pixels on edges, above chance) x (how light the inside of the outline is)
            x (hint prior). `hint` is the hint box in COARSE px (or None).
    """
    hh, ww = e_coarse.shape
    ub = uv.bounds
    tw, th = ub[2] - ub[0], ub[3] - ub[1]
    if tw <= 0 or th <= 0:
        return []
    diag = math.hypot(tw, th)
    samples = [shapely.get_coordinates(shapely.segmentize(uv, diag / 1000))]
    if uvb is not None:
        samples.append(shapely.get_coordinates(shapely.segmentize(uvb, diag / 1000)))
    pts = np.concatenate(samples)

    b = _dilate(e_coarse, 1).astype(np.float32)
    sh = (_fast_len(hh), _fast_len(ww))
    fb = np.fft.rfft2(b, sh)
    fl = np.fft.rfft2(light_coarse.astype(np.float32), sh)
    integ = np.zeros((hh + 1, ww + 1))
    integ[1:, 1:] = b.cumsum(0).cumsum(1)

    a_max = min(ww / tw, hh / th) * 0.999
    a_min = a_max * MIN_MAP_FRACTION
    out = []
    a = a_max
    while a >= a_min:
        px = np.round((pts[:, 0] - ub[0]) * a).astype(int)
        py = np.round((ub[3] - pts[:, 1]) * a).astype(int)
        w, h = int(px.max()) + 1, int(py.max()) + 1
        if w <= ww and h <= hh and _box_ok(w * f, h * f):
            t = np.zeros((h, w), np.float32)
            t[py, px] = 1
            n_t = float(t.sum())
            sl = (slice(0, hh - h + 1), slice(0, ww - w + 1))
            corr = np.fft.irfft2(fb * np.conj(np.fft.rfft2(t, sh)), sh)[sl]
            hit = corr / n_t
            box = integ[h:, w:][sl] - integ[:-h, w:][sl] - integ[h:, :-w][sl] + integ[:-h, :-w][sl]
            dens = box / (h * w)
            lift = (hit - dens) / (1 - dens + 0.05)

            # inside of the placed outline: is it light?
            placed = shapely.transform(
                uv, lambda c, a=a: np.column_stack([(c[:, 0] - ub[0]) * a, (ub[3] - c[:, 1]) * a])
            )
            fill = _raster(placed, (h, w)).astype(np.float32)
            n_f = float(fill.sum())
            if n_f > 0:
                lf = np.fft.irfft2(fl * np.conj(np.fft.rfft2(fill, sh)), sh)[sl] / n_f
                score = lift * (0.4 + 0.6 * np.clip(lf, 0, 1))
            else:
                score = lift

            if hint is not None:
                # bias towards where the map was seen (a negative score is left as it is)
                gy_, gx_ = score.shape
                prior = _hint_prior(
                    np.arange(gx_, dtype=float)[None, :],
                    np.arange(gy_, dtype=float)[:, None],
                    w,
                    h,
                    hint,
                )
                score = np.where(score > 0, score * prior, score)

            iy, ix = np.unravel_index(int(np.argmax(score)), score.shape)
            out.append(
                {
                    "score": float(score[iy, ix]),
                    "size": a * f * max(tw, th),
                    "a": a * f,
                    "tx": (ix - ub[0] * a) * f,
                    "ty": (iy + ub[3] * a) * f,
                }
            )
        a /= SCALE_STEP
    return out


# ── refinement ─────────────────────────────────────────────────────────────


def _nearest(tree, xy: np.ndarray, q: np.ndarray):
    """For every point of q: the nearest point of xy (indexed by the tree). Returns (coords, distances)."""
    if cKDTree is not None and isinstance(tree, cKDTree):
        d, i = tree.query(q)
        return xy[i], d
    ii, ti = tree.query_nearest(shapely.points(q), all_matches=False)
    near = np.zeros_like(q)
    d = np.full(len(q), np.inf)
    near[ii] = xy[ti]
    d[ii] = np.hypot(*(q[ii] - xy[ti]).T)
    return near, d


def _subsample(c: np.ndarray, n: int) -> np.ndarray:
    return c[:: max(1, len(c) // n)]


def _refine(e: np.ndarray, e_dil: np.ndarray, uv, uvb, init: dict) -> dict:
    """Annealed ICP of the outline (+ border) samples onto the edge pixels. x = a*u+tx, y = b*v+ty.

    This is only the rough placement now: _polish does the precise fit.
    """
    hgt, wid = e.shape
    ub = uv.bounds
    diag = math.hypot(ub[2] - ub[0], ub[3] - ub[1])
    po = _subsample(shapely.get_coordinates(shapely.segmentize(uv, diag / 150)), 900)
    pb = None
    if uvb is not None:
        pb = _subsample(shapely.get_coordinates(shapely.segmentize(uvb, diag / 200)), 900)

    a, tx, ty = init["a"], init["tx"], init["ty"]
    b = -a
    # edge pixels around the first placement only (the rest is clutter)
    x0, x1 = tx + a * ub[0], tx + a * ub[2]
    y0, y1 = ty - a * ub[3], ty - a * ub[1]
    pad = 0.2 * max(x1 - x0, y1 - y0)
    xs0, xs1 = int(max(0, x0 - pad)), int(min(wid, x1 + pad))
    ys0, ys1 = int(max(0, y0 - pad)), int(min(hgt, y1 + pad))
    ey, ex = np.nonzero(e[ys0:ys1, xs0:xs1])
    if len(ex) < 50:
        return {"a": a, "b": b, "tx": tx, "ty": ty, "hit_o": 0.0, "hit_b": None, "lift": -1.0}
    exy = _subsample(np.column_stack([ex + xs0 + 0.5, ey + ys0 + 0.5]), 20000)
    tree = cKDTree(exy) if cKDTree is not None else shapely.STRtree(shapely.points(exy))
    size = max(x1 - x0, y1 - y0)

    for i in range(ICP_ITERS):
        maxd = size * max(0.01, 0.06 * 0.9**i)
        near_o, d_o = _nearest(tree, exy, np.column_stack([a * po[:, 0] + tx, b * po[:, 1] + ty]))
        ko = d_o <= min(maxd, np.quantile(d_o[np.isfinite(d_o)], 0.75))
        xs, ys, tx_t, ty_t, w = (
            [po[ko, 0]],
            [po[ko, 1]],
            [near_o[ko, 0]],
            [near_o[ko, 1]],
            [np.ones(ko.sum())],
        )
        if pb is not None:
            near_b, d_b = _nearest(
                tree, exy, np.column_stack([a * pb[:, 0] + tx, b * pb[:, 1] + ty])
            )
            kb = d_b <= min(maxd, np.quantile(d_b[np.isfinite(d_b)], 0.6))
            if kb.sum() >= 10 and ko.sum() >= 10:
                xs.append(pb[kb, 0])
                ys.append(pb[kb, 1])
                tx_t.append(near_b[kb, 0])
                ty_t.append(near_b[kb, 1])
                w.append(np.full(kb.sum(), ko.sum() / kb.sum()))
        if sum(len(x) for x in xs) < 10:
            break
        wt = np.sqrt(np.concatenate(w))
        na, ntx = np.polyfit(np.concatenate(xs), np.concatenate(tx_t), 1, w=wt)
        nb, nty = np.polyfit(np.concatenate(ys), np.concatenate(ty_t), 1, w=wt)
        if na <= 0 or nb >= 0:
            break
        a, tx, b, ty = float(na), float(ntx), float(nb), float(nty)

    tol = max(2.0, 0.008 * size)
    _, d_o = _nearest(tree, exy, np.column_stack([a * po[:, 0] + tx, b * po[:, 1] + ty]))
    hit_o = float(np.mean(d_o <= tol))
    hit_b = None
    if pb is not None:
        _, d_b = _nearest(tree, exy, np.column_stack([a * pb[:, 0] + tx, b * pb[:, 1] + ty]))
        hit_b = float(np.mean(d_b <= tol))
    quality = hit_o if hit_b is None else 0.5 * (hit_o + hit_b)

    # chance level: how much of the placed box is "edge" anyway (a busy photo scores high for free)
    bx0, bx1 = int(max(0, tx + a * ub[0])), int(min(wid, tx + a * ub[2]))
    by0, by1 = int(max(0, ty + b * ub[3])), int(min(hgt, ty + b * ub[1]))
    box = e_dil[by0:by1, bx0:bx1]
    dens = float(box.mean()) if box.size else 1.0
    return {
        "a": a,
        "b": b,
        "tx": tx,
        "ty": ty,
        "hit_o": hit_o,
        "hit_b": hit_b,
        "lift": (quality - dens) / (1 - dens + 0.05),
    }


# ── polish: edge-only affine fit ───────────────────────────────────────────


def _edge_field(win: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Edge strength (0..1) and edge-LINE angle (radians, image axes: x right, y down) per pixel.

    Uses Lab colour so a pale map on pale sky still has an edge, and the structure tensor over the
    three channels so the direction is that of the strongest colour change. No "near light"
    restriction: borders between two painted regions count as well.
    """
    h, w = win.shape[:2]
    lab = _lab(win.reshape(-1, 3)).reshape(h, w, 3).astype(np.float32)
    if gaussian_filter is not None:
        lab = gaussian_filter(lab, (EDGE_PRE_SIGMA, EDGE_PRE_SIGMA, 0))
    gx = np.zeros_like(lab)
    gy = np.zeros_like(lab)
    gx[:, 1:-1] = lab[:, 2:] - lab[:, :-2]
    gy[1:-1] = lab[2:] - lab[:-2]
    jxx = (gx * gx).sum(2)
    jyy = (gy * gy).sum(2)
    jxy = (gx * gy).sum(2)
    root = np.sqrt((0.5 * (jxx - jyy)) ** 2 + jxy**2)
    mag = np.sqrt(0.5 * (jxx + jyy) + root)
    grad_angle = 0.5 * np.arctan2(2 * jxy, jxx - jyy)  # direction of the colour change
    strength = np.clip((mag - EDGE_LAB_LO) / (EDGE_LAB_HI - EDGE_LAB_LO), 0, 1)
    return strength.astype(np.float32), (grad_angle + np.pi / 2).astype(np.float32)


def _bin_fields(strength: np.ndarray, phi: np.ndarray, sigma: float) -> np.ndarray:
    """(N_BINS, h, w): for each line direction, the blurred strength of the edges that run that way."""
    out = np.empty((N_BINS, *strength.shape), np.float32)
    step = np.pi / N_BINS
    norm = sigma * math.sqrt(2 * math.pi)  # a 1 px ridge then peaks at about 1

    def one(k: int) -> None:
        compat = np.maximum(0.0, np.cos(2 * (phi - k * step))) ** ALIGN_POWER
        out[k] = gaussian_filter(strength * compat, sigma, mode="constant") * norm

    list(_POOL.map(one, range(N_BINS)))
    return out


def _line_parts(g) -> list:
    out = []
    stack = [g]
    while stack:
        x = stack.pop()
        if x is None or x.is_empty:
            continue
        kind = x.geom_type
        if kind in {"LineString", "LinearRing"}:
            out.append(x)
        elif kind == "Polygon":
            out.append(shapely.LineString(x.exterior.coords))
            out.extend(shapely.LineString(r.coords) for r in x.interiors)
        elif hasattr(x, "geoms"):
            stack.extend(x.geoms)
    return out


def _samples(geom, spacing: float) -> tuple[np.ndarray, np.ndarray]:
    """Evenly spaced points along the lines of a plane geometry, with the unit tangent at each."""
    parts = np.array(_line_parts(geom) if geom is not None else [], dtype=object)
    if len(parts):
        lens = shapely.length(parts)
        parts = parts[lens >= spacing * 3]
    if not len(parts):
        return np.empty((0, 2)), np.empty((0, 2))
    lens = shapely.length(parts)
    closed = shapely.is_closed(parts)
    n = np.maximum(8, (lens / spacing).astype(int))
    line = np.repeat(np.arange(len(parts)), n)
    first = np.cumsum(n) - n
    step = lens / np.where(closed, n, n - 1)  # linspace: the end point is only kept on open lines
    d = (np.arange(len(line)) - first[line]) * step[line]
    length, shut, geoms = lens[line], closed[line], parts[line]

    def at(dd):
        dd = np.where(shut, dd % length, np.clip(dd, 0.0, length))
        return shapely.get_coordinates(shapely.line_interpolate_point(geoms, dd))

    p, a, b = at(d), at(d - spacing), at(d + spacing)
    t = b - a
    nrm = np.hypot(t[:, 0], t[:, 1])
    ok = nrm > 1e-12
    return p[ok], t[ok] / nrm[ok, None]


def _compose(p: np.ndarray, ctx: dict) -> tuple[np.ndarray, np.ndarray]:
    """P = (dx, dy, log sx, log sy, rotation, shear): a correction applied in px space, around the
    centre of the current placement. Returns the full transform plane -> px: px = A @ uv + t.
    """
    dx, dy, ls, lt, th, sh = p
    c, s = math.cos(th), math.sin(th)
    m = np.array([[c, -s], [s, c]]) @ np.array([[math.exp(ls), sh], [0.0, math.exp(lt)]])
    a = m @ ctx["A0"]
    t = m @ (ctx["t0"] - ctx["c"]) + ctx["c"] + np.array([dx, dy])
    return a, t


def _place(pts: np.ndarray, tans: np.ndarray, a: np.ndarray, t: np.ndarray):
    """Sample positions in px and the direction bin of the line at each."""
    xy = pts @ a.T + t
    tv = tans @ a.T
    ang = np.arctan2(tv[:, 1], tv[:, 0]) % np.pi
    return xy, np.rint(ang / (np.pi / N_BINS)).astype(int) % N_BINS


def _bilinear(f: np.ndarray, k: np.ndarray, x: np.ndarray, y: np.ndarray) -> np.ndarray:
    """f[k] sampled at window coordinates (pixel centres at index + 0.5). Outside the window: 0."""
    _nk, h, w = f.shape
    fx, fy = x - 0.5, y - 0.5
    x0, y0 = np.floor(fx).astype(int), np.floor(fy).astype(int)
    ax, ay = fx - x0, fy - y0
    xs = np.stack([x0, x0 + 1, x0, x0 + 1])
    ys = np.stack([y0, y0, y0 + 1, y0 + 1])
    ok = (xs >= 0) & (xs < w) & (ys >= 0) & (ys < h)
    idx = np.where(ok, k * (h * w) + ys * w + xs, 0)
    v = np.where(ok, f.reshape(-1)[idx], 0.0)
    wt = np.stack([(1 - ax) * (1 - ay), ax * (1 - ay), (1 - ax) * ay, ax * ay])
    return (v * wt).sum(0)


_PARAM_SCALE = np.array([1.0, 1.0, 0.01, 0.01, 0.005, 0.005])  # optimiser units -> px / log / rad


def _objective(z: np.ndarray, f: np.ndarray, ctx: dict) -> float:
    p = z * _PARAM_SCALE
    lim = ctx["lim"]
    over = np.maximum(0.0, np.abs(p) / lim - 1.0)
    wall = 1e3 * float((over**2).sum())
    a, t = _compose(p, ctx)
    xy, k = _place(ctx["P"], ctx["T"], a, t)
    v = _bilinear(f, k, xy[:, 0] - ctx["ox"], xy[:, 1] - ctx["oy"])
    score = float((ctx["W"] * v).sum() / ctx["Wsum"])
    return -score + POLISH_REG * float(((p / lim) ** 2).sum()) + wall


def _honest(strength: np.ndarray, phi: np.ndarray, ctx: dict, a: np.ndarray, t: np.ndarray) -> dict:
    """The displayed match for a placement. Per line sample: distance (px) to the nearest strong edge
    whose direction matches the line. Compared with the same measure for the line shifted a few px.
    """
    h, w = strength.shape
    xy, k = _place(ctx["P"], ctx["T"], a, t)
    xy -= np.array([ctx["ox"], ctx["oy"]])
    step = np.pi / N_BINS
    strong = strength >= STRONG_W

    def edt(kk: int) -> np.ndarray:
        m = strong & (np.cos(2 * (phi - kk * step)) >= ALIGN_COS)
        if not m.any():
            return np.full((h, w), 99.0, np.float32)
        return distance_transform_edt(~m).astype(np.float32)

    bins = [int(v) for v in np.unique(k)]
    dist = dict(zip(bins, _POOL.map(edt, bins), strict=True))

    def lookup(pts: np.ndarray) -> np.ndarray:
        ix = np.rint(pts[:, 0] - 0.5).astype(int)
        iy = np.rint(pts[:, 1] - 0.5).astype(int)
        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        d = np.full(len(pts), np.nan, np.float32)
        for kk, grid in dist.items():
            sel = ok & (k == kk)
            d[sel] = np.minimum(grid[iy[sel], ix[sel]], 99.0)
        return d

    shift = float(np.clip(CHANCE_SHIFT_FRAC * ctx["size"], CHANCE_SHIFT_MIN, CHANCE_SHIFT_MAX))
    shifts = [
        np.array([shift * math.cos(ang), shift * math.sin(ang)])
        for ang in np.arange(8) * math.pi / 4
    ]
    d0 = lookup(xy)
    ds = [lookup(xy + s) for s in shifts]
    wgt, grp = ctx["W"], ctx["G"]
    tol = MATCH_TOL_PX

    def stat(d: np.ndarray, mask: np.ndarray):
        ok = mask & np.isfinite(d)
        if ok.sum() < 10:
            return None
        dv = d[ok]
        return (
            float(np.average(dv <= tol, weights=wgt[ok])),
            float(np.median(dv)),
            float(np.quantile(dv, 0.9)),
        )

    def reverse(pts: np.ndarray):
        """Share of the strong image edges next to our line that our line explains."""
        ix = np.rint(pts[:, 0] - 0.5).astype(int)
        iy = np.rint(pts[:, 1] - 0.5).astype(int)
        ok = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        if ok.sum() < 10:
            return None
        line = np.zeros((h, w), bool)
        line[iy[ok], ix[ok]] = True
        dl = distance_transform_edt(~line)
        band = strong & (dl <= BAND_PX)
        if band.sum() < 10:
            return None
        return float(np.mean(dl[band] <= tol))

    def group(mask: np.ndarray, with_reverse: bool):
        base = stat(d0, mask)
        if base is None:
            return None
        chance = [c for c in (stat(d, mask) for d in ds) if c is not None]
        out = {
            "precision": base[0],
            "median": base[1],
            "p90": base[2],
            "tol": tol,
            "chance_precision": float(np.mean([c[0] for c in chance])) if chance else 0.0,
            "chance_median": float(np.median([c[1] for c in chance])) if chance else None,
        }
        if with_reverse:
            out["reverse"] = reverse(xy[mask])
        return out

    def lift(g: dict | None) -> float:
        if g is None:
            return -1.0
        c = g["chance_precision"]
        return (g["precision"] - c) / (1 - c + 0.05)

    o = group(~grp, True)
    b = group(grp, True) if grp.any() else None
    pooled = group(np.ones(len(grp), bool), False)
    return {"o": o, "b": b, "lift": lift(o), "score": lift(pooled)}


def _polish(sub: np.ndarray, uv, uvb, r: dict) -> dict | None:
    """Full-affine refinement of a rough placement (r: a, b, tx, ty from ICP). Edge-only.

    Returns {A, t, honest}: the better (by the honest, chance-corrected match) of the rough
    placement and the polished one, or None when it can't run (no scipy, map too small).
    """
    if gaussian_filter is None or distance_transform_edt is None or minimize is None:
        return None
    hgt, wid = sub.shape[:2]
    a0, b0, tx, ty = r["a"], r["b"], r["tx"], r["ty"]
    ub = uv.bounds
    xs = sorted((tx + a0 * ub[0], tx + a0 * ub[2]))
    ys = sorted((ty + b0 * ub[1], ty + b0 * ub[3]))
    size = max(xs[1] - xs[0], ys[1] - ys[0])
    if size < 24:
        return None
    sigmas = [
        min(cap, max(POLISH_SIGMA_MIN, frac * size))
        for frac, cap in zip(POLISH_SIGMA_FRAC, POLISH_SIGMA_CAP, strict=True)
    ]
    pad = int(0.12 * size + 3 * sigmas[0]) + 4
    wx0, wx1 = max(0, int(xs[0] - pad)), min(wid, int(xs[1] + pad) + 1)
    wy0, wy1 = max(0, int(ys[0] - pad)), min(hgt, int(ys[1] + pad) + 1)
    if wx1 - wx0 < 16 or wy1 - wy0 < 16:
        return None
    strength, phi = _edge_field(sub[wy0:wy1, wx0:wx1])

    spacing = math.hypot(ub[2] - ub[0], ub[3] - ub[1]) / SAMPLES_PER_DIAG
    p_o, t_o = _samples(uv, spacing)
    p_b, t_b = _samples(uvb, spacing)
    if len(p_o) < 50:
        return None
    pts = np.vstack([p_o, p_b])
    tans = np.vstack([t_o, t_b])
    weight = np.concatenate([np.ones(len(p_o)), np.full(len(p_b), POLISH_BORDER_W)])
    is_border = np.concatenate([np.zeros(len(p_o), bool), np.ones(len(p_b), bool)])
    ctx = {
        "P": pts,
        "T": tans,
        "W": weight,
        "Wsum": float(weight.sum()),
        "G": is_border,
        "A0": np.diag([a0, b0]),
        "t0": np.array([tx, ty]),
        "c": np.array([(xs[0] + xs[1]) / 2, (ys[0] + ys[1]) / 2]),
        "ox": wx0,
        "oy": wy0,
        "size": size,
        "lim": np.array([0.06 * size, 0.06 * size, 0.08, 0.08, 0.06, 0.06]),
    }

    z = np.zeros(6)
    for li, sg in enumerate(sigmas):
        field = _bin_fields(strength, phi, sg)
        steps = np.array([max(0.7, 0.8 * sg)] * 2 + [1.0] * 4)
        for _ in range(2 if li == len(sigmas) - 1 else 1):
            simplex = np.vstack([z, *(z + np.eye(6)[i] * steps[i] for i in range(6))])
            res = minimize(
                _objective,
                z,
                args=(field, ctx),
                method="Nelder-Mead",
                options={"initial_simplex": simplex, "maxiter": 600, "xatol": 0.02, "fatol": 1e-6},
            )
            z = res.x

    a_new, t_new = _compose(z * _PARAM_SCALE, ctx)
    before = _honest(strength, phi, ctx, ctx["A0"], ctx["t0"])
    after = _honest(strength, phi, ctx, a_new, t_new)
    if after["score"] >= before["score"]:
        return {"A": a_new, "t": t_new, "honest": after}
    return {"A": ctx["A0"], "t": ctx["t0"], "honest": before}


def _prep_outline(outline):
    """(fit, clip, extra): the outline used to MATCH the screenshot (small islands dropped: they only add
    noise), the outline used to clip the results, and the small islands the fit leaves out (None when
    there are none). `extra` is where stains on those islands are read.

    The outline already comes simplified and valid (scripts/convert_geo.py), so nothing is simplified
    or repaired here: the parts are just sorted by size.
    """
    parts = shapely.get_parts(outline)
    parts = parts[shapely.get_type_id(parts) == 3]  # polygons only
    if not len(parts):
        msg = "That country has no outline to match against"
        raise StainError(msg)
    area = shapely.area(parts)
    big = parts[area >= 0.02 * float(area.max())]
    small = parts[area < 0.02 * float(area.max())]
    clip = outline if shapely.is_valid(outline) else shapely.make_valid(outline)
    return shapely.multipolygons(big), clip, (shapely.multipolygons(small) if len(small) else None)


def register(sub: np.ndarray, outline, borders, projection: str, hint=None) -> dict:
    """Find where the country is in `sub` and the plane <-> image transform. Crop-local.

    The coarse search runs for every projection that makes sense for the country's bounds
    (equirectangular, plain lon/lat, mercator, and the two conics for mid-latitude countries);
    the best few placements are refined (ICP, then the edge-only affine polish) and the one with
    the best chance-corrected edge match wins.

    The transform is px = A @ (u, v) + t with a full 2x2 A.

    hint = (x0, y0, x1, y1) in `sub` px: a rough box of where a map was seen. It biases the coarse
    search and nudges the final choice; it never excludes a placement.
    """
    hgt, wid = sub.shape[:2]
    fit_outline, clip_outline, extra_outline = _prep_outline(outline)
    bounds = fit_outline.bounds
    inner = None
    if borders is not None and not borders.is_empty:
        ob = fit_outline.bounds
        eps = 0.01 * math.hypot(ob[2] - ob[0], ob[3] - ob[1])
        try:
            inner = shapely.intersection(borders, shapely.buffer(fit_outline, -eps))
        except Exception:  # noqa: BLE001
            inner = None
        if inner is not None and inner.is_empty:
            inner = None

    # A drawn map sits on light colours: keep only the edges that touch a light pixel.
    # (Only for the coarse search and ICP; the polish uses Lab edges without this restriction.)
    light = sub.min(2) >= LIGHT_MIN
    e = _edges(sub)
    near_light = e & _dilate(light, 2)
    if near_light.sum() >= 200:
        e = near_light
    e_dil = _dilate(e, 2)
    # distance (px) from every pixel to the nearest drawn edge: only used by the fallback metric
    dt = distance_transform_edt(~e) if distance_transform_edt is not None and e.any() else None
    f = max(1, math.ceil(max(hgt, wid) / COARSE_MAX))
    hc, wc = hgt // f, wid // f
    e_coarse = e[: hc * f, : wc * f].reshape(hc, f, wc, f).max((1, 3))
    light_coarse = light[: hc * f, : wc * f].reshape(hc, f, wc, f).mean((1, 3)) >= 0.5
    hint_coarse = None if hint is None else tuple(v / f for v in hint)

    names = list(PROJECTIONS) if projection == "auto" else [projection]
    cands = []
    for nm in names:
        pr = _projection(nm, bounds) or (
            _projection("equirect", bounds) if projection != "auto" else None
        )
        if pr is None:
            continue
        fwd, inv = pr
        uv = shapely.transform(fit_outline, fwd)
        uvb = shapely.transform(inner, fwd) if inner is not None else None
        cands.extend(
            {**c, "name": nm, "uv": uv, "uvb": uvb, "inv": inv, "fwd": fwd}
            for c in _candidates(e_coarse, light_coarse, f, uv, uvb, hint_coarse)
        )
    if not cands:
        msg = "The map doesn't fit in this crop. Use a bigger crop."
        raise StainError(msg)

    # best few placements, skipping near-duplicates (same projection, similar scale and spot)
    cands.sort(key=lambda c: -c["score"])
    chosen: list[dict] = []
    for c in cands:
        if all(
            not (
                c["name"] == k["name"]
                and 0.9 < c["a"] / k["a"] < 1.1
                and abs(c["tx"] - k["tx"]) < 0.1 * k["size"]
                and abs(c["ty"] - k["ty"]) < 0.1 * k["size"]
            )
            for k in chosen
        ):
            chosen.append(c)
        if len(chosen) >= TOP_K:
            break

    refined = []
    for c in chosen:
        r = _refine(e, e_dil, c["uv"], c["uvb"], c)
        pol = _polish(sub, c["uv"], c["uvb"], r)
        if pol is not None:
            h = pol["honest"]
            r["A"], r["t"], r["honest"] = pol["A"], pol["t"], h
            r["m_o"], r["m_b"] = h["o"], h["b"]
            r["lift"], r["score"] = h["lift"], h["score"]
            r["hit_o"] = h["o"]["precision"] if h["o"] else 0.0
            r["hit_b"] = h["b"]["precision"] if h["b"] else None
        else:
            r["A"] = np.diag([r["a"], r["b"]])
            r["t"] = np.array([r["tx"], r["ty"]])
            m = [r["a"], 0, 0, r["b"], r["tx"], r["ty"]]
            r["m_o"] = _line_match(affine_transform(c["uv"], m), dt)
            r["m_b"] = (
                _line_match(affine_transform(c["uvb"], m), dt) if c["uvb"] is not None else None
            )
            r["score"] = r["lift"]
        bx0, by0, bw, bh = _placed_box(c["uv"], r["A"], r["t"])
        if not _box_ok(bw, bh):
            continue
        r["prior"] = float(_hint_prior(bx0, by0, bw, bh, hint))
        refined.append(
            {
                **r,
                "name": c["name"],
                "uv": c["uv"],
                "uvb": c["uvb"],
                "inv": c["inv"],
                "fwd": c["fwd"],
            }
        )

    if not refined:
        msg = "The map came out too small (under 100 px on a side, or under 300 px on its longest). Use a bigger crop or image."
        raise StainError(msg)

    def hint_nudge(r: dict) -> float:
        return 1 - HINT_FINAL_WEIGHT + HINT_FINAL_WEIGHT * r.get("prior", 1.0)

    if any(r.get("honest") for r in refined):
        # The honest, chance-corrected edge match decides; the hint only breaks near-ties.
        best = max(refined, key=lambda r: (r["score"] if r.get("honest") else -9.0) * hint_nudge(r))
    else:
        # No scipy: the old rule. The displayed match is what we optimise; the chance-corrected
        # lift only guards against a busy photo scoring well by accident.
        top_lift = max(r["lift"] for r in refined)
        pool = [r for r in refined if r["lift"] >= MATCH_LIFT_KEEP * top_lift] or refined
        best = max(pool, key=_match_key)

    amat, tvec = np.asarray(best["A"], float), np.asarray(best["t"], float)
    m = [amat[0, 0], amat[0, 1], amat[1, 0], amat[1, 1], tvec[0], tvec[1]]
    return {
        **{k: best[k] for k in ("hit_o", "hit_b", "lift", "name", "m_o", "m_b", "prior")},
        "A": amat,
        "t": tvec,
        "inv": best["inv"],
        "placed": affine_transform(best["uv"], m),
        "placed_b": affine_transform(best["uvb"], m) if best["uvb"] is not None else None,
        # small islands the fit outline leaves out, placed with the same projection and transform
        "placed_extra": (
            None
            if extra_outline is None
            else affine_transform(shapely.transform(extra_outline, best["fwd"]), m)
        ),
        "clip_outline": clip_outline,
        "fit_outline": fit_outline,
        "dt": dt,
    }


# ── stains (inside the matched shape only, full resolution) ────────────────


def _dull_mask(rgb: np.ndarray) -> np.ndarray:
    """(N, 3) RGB -> bool per colour: grey / black / white, or a very light blue. Colours that are
    not painted stains (text, borders, water, paper).
    """
    c = rgb.astype(np.float32)
    r, g, b = c[:, 0], c[:, 1], c[:, 2]
    mx, mn = c.max(1), c.min(1)
    spread = mx - mn
    dull = (spread <= GREY_SPREAD) | (mx < BLACK_MAX)
    safe = np.where(spread > 0, spread, 1.0)
    hue = (
        np.where(
            mx == r,
            ((g - b) / safe) % 6,
            np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4),
        )
        * 60.0
    )
    sat = np.where(mx > 0, spread / np.maximum(mx, 1.0), 0.0)
    light_blue = (
        (hue >= LIGHT_BLUE_HUE[0])
        & (hue <= LIGHT_BLUE_HUE[1])
        & (sat <= LIGHT_BLUE_SAT_MAX)
        & (mx / 255.0 >= LIGHT_BLUE_VAL_MIN)
    )
    return dull | light_blue


def _grey_mask(rgb: np.ndarray) -> np.ndarray:
    """(N, 3) RGB -> bool per colour: greyish / dark pixels (inner borders, text), not white paper
    and not light blue.
    """
    c = rgb.astype(np.float32)
    mx, mn = c.max(1), c.min(1)
    return ((mx - mn) <= GREY_SPREAD) & (mx < BORDER_WHITE_MIN)


def _cross_dilate(m: np.ndarray) -> np.ndarray:
    """One pixel layer of growth (up, down, left, right)."""
    out = m.copy()
    out[1:] |= m[:-1]
    out[:-1] |= m[1:]
    out[:, 1:] |= m[:, :-1]
    out[:, :-1] |= m[:, 1:]
    return out


def _small_pieces(lab: np.ndarray, k: int) -> np.ndarray:
    """Mask of the pixels of label k that sit in small or thin pieces (anti-aliasing specks).

    A piece is small when it has fewer than SMALL_PIECE_PX pixels, or when it has no MIN_STAIN_WIDTH_PX
    square solid block (a line 3 px wide or less, such as the fringe around a stain).
    """
    m = lab == k
    if not m.any() or nd_label is None:
        return np.zeros_like(m)
    comp, n = nd_label(m, structure=np.ones((3, 3), bool))
    flat = comp.ravel()
    sizes = np.bincount(flat, minlength=n + 1)
    blocks = _solid_blocks(m, MIN_STAIN_WIDTH_PX)
    solid = (
        np.bincount(
            comp[: blocks.shape[0], : blocks.shape[1]].ravel(),
            weights=blocks.ravel(),
            minlength=n + 1,
        )
        > 0
    )
    small = (sizes < SMALL_PIECE_PX) | ~solid
    small[0] = False
    return small[comp]


def _fragmented(lab: np.ndarray, k: int) -> bool:
    """True when most of label k's pixels are in small or thin pieces (anti-aliasing, not a stain).

    Islands are not penalised for being many: only the share of the stain's pixels that sit in
    small / thin pieces counts.
    """
    total = int((lab == k).sum())
    if not total:
        return False
    return float(_small_pieces(lab, k).sum()) > FRAGMENT_SHARE * total


def _hand_specks_to_nearest(lab: np.ndarray, order: list[int]) -> None:
    """`order` = stains from the darkest to the lightest. The small / thin pieces of every stain are
    usually the blend between two stains (anti-aliasing). Each such piece joins the closest NON-small
    stain (within SPECK_REASSIGN_PX); a tie goes to the darker stain. When no stain is that close it
    becomes undefined. Works in place.
    """
    if distance_transform_edt is None or not order:
        return
    h, w = lab.shape
    specks = np.zeros(lab.shape, bool)
    for k in order:
        specks |= _small_pieces(lab, k)
    if not specks.any():
        return
    solid = np.where(np.isin(lab, order) & ~specks, lab, -1)
    ys, xs = np.nonzero(specks)
    pad = SPECK_REASSIGN_PX + 1
    y0, y1 = max(0, ys.min() - pad), min(h, ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(w, xs.max() + pad + 1)
    sub = lab[y0:y1, x0:x1]
    sp = specks[y0:y1, x0:x1]
    sol = solid[y0:y1, x0:x1]
    far = np.full(sub.shape, np.inf)
    # distance of every pixel to the nearest solid pixel of each stain (darkest first: argmin keeps ties dark)
    dist = np.stack([distance_transform_edt(sol != k) if (sol == k).any() else far for k in order])
    best = dist.argmin(0)
    near = sp & (dist.min(0) <= SPECK_REASSIGN_PX)
    sub[near] = np.asarray(order, dtype=sub.dtype)[best][near]
    sub[sp & ~near] = -1


def _expand(
    lab: np.ndarray, order: list[int], points: int, allowed: np.ndarray, resistance: np.ndarray
) -> None:
    """Grow the stains of `order` (darkest first) into undefined pixels of `allowed`. Works in place.

    Every stain has `points` to spend. Stepping onto an undefined pixel costs its `resistance` (1 = normal),
    and the cost adds up along the way: a pixel is taken when the cheapest way to it from the stain's own
    pixels is at most `points`. Time advances one point at a time, so stains still grow side by side.
    """
    big = points + 1
    cost = np.where(lab >= 0, 0, big).astype(np.int16)  # points spent to reach each labelled pixel
    for t in range(1, points + 1):
        if not ((lab < 0) & allowed).any():
            break
        for k in order:
            m = lab == k
            if not m.any():
                continue
            src = np.where(m, cost, big).astype(np.int16)
            nb = np.full(lab.shape, big, np.int16)  # cheapest 4-neighbour of this stain
            nb[1:] = np.minimum(nb[1:], src[:-1])
            nb[:-1] = np.minimum(nb[:-1], src[1:])
            nb[:, 1:] = np.minimum(nb[:, 1:], src[:, :-1])
            nb[:, :-1] = np.minimum(nb[:, :-1], src[:, 1:])
            arrive = nb + resistance
            take = (lab < 0) & allowed & (nb < big) & (arrive <= t)
            cost[take] = arrive[take]
            lab[take] = k


def _hex_lab(colour: str) -> np.ndarray:
    """'#RRGGBB' -> CIE Lab."""
    rgb = [int(colour.lstrip("#")[i : i + 2], 16) for i in (0, 2, 4)]
    return _lab(np.array([rgb]))[0].astype(np.float64)


def _solid_blocks(m: np.ndarray, k: int) -> np.ndarray:
    """Top-left corners of the k x k blocks that lie fully inside the mask."""
    h, w = m.shape
    if h < k or w < k:
        return np.zeros((0, 0), bool)
    rows = np.ones((h, w - k + 1), bool)
    for s in range(k):
        rows &= m[:, s : w - k + 1 + s]
    out = np.ones((h - k + 1, w - k + 1), bool)
    for s in range(k):
        out &= rows[s : h - k + 1 + s]
    return out


def _fill_gaps(lab: np.ndarray, region: np.ndarray, max_area: float) -> None:
    """Give the stains the small undefined gaps that growth left behind. Works in place.

    A gap is a connected group of undefined pixels inside `region`. It is filled when it has at most
    `max_area` px, touches a stain and does not touch the base map; every pixel takes the nearest stain.
    Gaps next to the base are left alone (they end up as base).
    """
    if nd_label is None or distance_transform_edt is None or max_area <= 0:
        return
    gaps = (lab < 0) & region
    if not gaps.any():
        return
    comp, n = nd_label(gaps)
    base_adj = np.zeros(lab.shape, bool)
    stain_adj = np.zeros(lab.shape, bool)
    for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
        nb = _shifted(lab, dy, dx)
        base_adj |= gaps & (nb == 0)
        stain_adj |= gaps & (nb > 0)
    flat = comp.ravel()
    sizes = np.bincount(flat, minlength=n + 1)
    has_base = np.bincount(flat, weights=base_adj.ravel(), minlength=n + 1) > 0
    has_stain = np.bincount(flat, weights=stain_adj.ravel(), minlength=n + 1) > 0
    fill = (sizes <= max_area) & has_stain & ~has_base
    fill[0] = False
    todo = fill[comp]
    if todo.any():
        nearest = _fill_nearest(np.where(lab > 0, lab, -1).astype(lab.dtype), todo)
        lab[todo] = nearest[todo]


CLOSE_STAIN_PX = 4  # undefined channels up to about 2x this wide between parts of a stain are closed (full-res px)


def _close_stains(lab: np.ndarray, region: np.ndarray, r: int) -> None:
    """Morphological closing of every stain, applied only to undefined pixels. Works in place.

    A stain is grown by `r` px and shrunk back by `r` px: that bridges narrow undefined channels and slivers
    (also the ones open to the outside, which _fill_gaps ignores) without growing its outer edge. Base and
    other stains are never overwritten. A pixel several stains could close takes the nearest one.
    """
    if r <= 0 or distance_transform_edt is None:
        return
    undefined = (lab < 0) & region
    if not undefined.any():
        return
    best = np.full(lab.shape, np.inf)
    pick = np.full(lab.shape, -1, lab.dtype)
    for k in np.unique(lab[lab > 0]):
        m = lab == k
        cand = _erode(_dilate(m, r), r) & undefined
        if not cand.any():
            continue
        d = distance_transform_edt(~m)
        take = cand & (d < best)
        best[take] = d[take]
        pick[take] = k
    lab[pick > 0] = pick[pick > 0]


def _classify(
    img: np.ndarray,
    region: np.ndarray,
    delta_e: float,
    max_classes: int,
    expand_px: int,
    border_inset: int = 0,
    hole_max: float = 0.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Label every pixel of `region`. Returns (labels (h, w) int16, colours (K, 3)).

    Label 0 is the base map, 1.. are the stains; -1 is outside `region`. Pixels inside `region` never
    stay undefined: what no stain claims goes to the base.
    """
    h, w = region.shape
    idx = np.flatnonzero(region.ravel())
    if not len(idx):
        msg = "No colours were found inside the map."
        raise StainError(msg)
    pix = img.reshape(-1, 3)[idx].astype(np.int32)
    key = (pix[:, 0] << 16) | (pix[:, 1] << 8) | pix[:, 2]
    ukey, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
    inv = inv.ravel()
    ucol = np.column_stack([ukey >> 16, (ukey >> 8) & 255, ukey & 255]).astype(np.int16)
    ulab = _lab(ucol).astype(np.float64)
    dull = _dull_mask(ucol)
    _grey_mask(ucol)
    pale = (ulab[:, 0] >= BASE_L_MIN) & (np.hypot(ulab[:, 1], ulab[:, 2]) <= BASE_CHROMA_MAX)

    order = np.argsort(-cnt, kind="stable")  # most common colour first
    pale_first = order[pale[order]]
    base = int(pale_first[0]) if len(pale_first) else int(order[0])
    c32 = ucol.astype(np.float32)
    water = dull & ~(((c32.max(1) - c32.min(1)) <= GREY_SPREAD) | (c32.max(1) < BLACK_MAX))

    # Every colour gets the FIRST tier of COLOR_TIERS it fits (one answer near the outline, one inside).
    tiers = list(COLOR_TIERS)
    if not any(t[0] == "undefined" for t in tiers):
        tiers.append(("undefined", "dynamic", 0.0, False, 2))
    names = [t[0] for t in tiers]
    base_lab = ulab[base]
    if "base" in names and tiers[names.index("base")][1] != "dynamic":
        base_lab = _hex_lab(tiers[names.index("base")][1])
        base = int(_delta_e(ulab, base_lab).argmin())
    n_u = len(ukey)
    tier_out = np.full(n_u, -1, np.int16)
    tier_in = np.full(n_u, -1, np.int16)
    centres = [base]
    dist = None
    for ti, (name, colour, de, only_inner, _res) in enumerate(tiers):
        d = delta_e if de is None else de
        fixed = None if colour == "dynamic" else _hex_lab(colour)
        if name == "base":
            m = _delta_e(ulab, base_lab) <= d
            if fixed is None:
                m[base] = True
        elif name == "water":
            m = water if fixed is None else _delta_e(ulab, fixed) <= d
        elif name == "stain":
            # walk down the unclaimed colours; one far from everything chosen so far starts a new stain
            claimed = (tier_out >= 0) | (tier_in >= 0)
            left = order[~dull[order] & ~claimed[order]]
            left = left[left != base]
            left = left[_delta_e(ulab[left], base_lab) > d]
            stains: list[int] = []
            while len(left) and len(stains) < max_classes:
                c = int(left[0])
                stains.append(c)
                left = left[_delta_e(ulab[left], ulab[c]) > d]
            centres = [base, *stains]
            dist = np.stack([_delta_e(ulab, ulab[c]) for c in centres], axis=1)
            m = (dist[:, 1:].min(1) <= d) if stains else np.zeros(n_u, bool)
        elif name == "undefined":
            m = np.ones(n_u, bool)
        else:
            m = np.zeros(n_u, bool) if fixed is None else _delta_e(ulab, fixed) <= d
        if not only_inner:
            tier_out[m & (tier_out < 0)] = ti
        tier_in[m & (tier_in < 0)] = ti
    tier_out[tier_out < 0] = names.index("undefined")

    is_base = np.array([x == "base" for x in names])
    is_stain = np.array([x == "stain" for x in names])
    stain_lbl = np.zeros(n_u, np.int16)
    if dist is not None and dist.shape[1] > 1:
        stain_lbl = (1 + dist[:, 1:].argmin(1)).astype(np.int16)

    def label_of(t: np.ndarray) -> np.ndarray:
        return np.where(is_base[t], 0, np.where(is_stain[t], stain_lbl, -1)).astype(np.int16)

    undef_res = tiers[names.index("undefined")][4]
    res_t = np.array(
        [undef_res if x == "stain" else t[4] for x, t in zip(names, tiers, strict=True)]
    )
    res_t = np.where(res_t < 0, 1000, np.maximum(res_t, 1)).astype(np.int16)
    inner = _erode(region, border_inset).ravel()[idx]
    pix_tier = np.where(inner, tier_in[inv], tier_out[inv])
    ulabel = label_of(tier_out)

    lab = np.full(h * w, -1, np.int16)
    lab[idx] = np.where(inner, label_of(tier_in)[inv], ulabel[inv])
    lab = lab.reshape(h, w)
    resistance = np.ones(h * w, np.int16)
    resistance[idx] = res_t[pix_tier]
    resistance = resistance.reshape(h, w)
    _log_colours(ucol, ulab, cnt, dull, pale, ulabel, centres, delta_e, max_classes)
    _log_counts("initial", lab, region, ucol[centres])

    # a stain made of small / thin disconnected pieces is anti-aliasing: all of it becomes undefined
    for k in range(1, len(centres)):
        if _fragmented(lab, k):
            logger.info(
                "classify: stain %d dropped as fragmented (%d px)", k, int((lab == k).sum())
            )
            lab[lab == k] = -1
    _log_counts("after fragment filter", lab, region, ucol[centres])

    # the remaining stains grow into undefined pixels, darkest first; the rest goes to the base
    alive = [k for k in range(1, len(centres)) if (lab == k).any()]
    alive.sort(key=lambda k: ulab[centres[k], 0])
    # a piece 3 px wide or less is not a stain: it becomes undefined (growth below may still take it)
    for k in alive:
        lab[_small_pieces(lab, k)] = -1
    _log_counts("after thin pieces", lab, region, ucol[centres])
    # the resistance of every pixel comes from its colour tier (COLOR_TIERS)
    _expand(lab, alive, expand_px, region, resistance)
    # undefined gaps the stains did not reach are theirs when nothing else touches them
    _fill_gaps(lab, region, hole_max)
    # narrow channels and slivers between parts of a stain (also open ones) are closed
    _close_stains(lab, region, CLOSE_STAIN_PX)
    _log_counts("after expansion", lab, region, ucol[centres])
    lab[(lab < 0) & region] = 0
    _log_counts("final", lab, region, ucol[centres])
    return lab, ucol[centres]


def _log_colours(
    ucol, ulab, cnt, dull, pale, ulabel, centres, delta_e, max_classes, top=30
) -> None:
    """Debug: the most common exact colours of the map and what the classifier did with each."""
    total = int(cnt.sum()) or 1
    rows = []
    for i in np.argsort(-cnt, kind="stable")[:top]:
        de = float(_delta_e(ulab[i], ulab[centres[0]]))
        rows.append(
            f"  {_hex(ucol[i])} n={int(cnt[i])} ({100 * cnt[i] / total:.2f}%) "
            f"dull={bool(dull[i])} pale={bool(pale[i])} dE_to_base={de:.1f} label={int(ulabel[i])}"
        )
    picked = ", ".join(
        f"{'base' if j == 0 else f'stain {j}'}={_hex(ucol[c])}" for j, c in enumerate(centres)
    )
    logger.info(
        "classify: delta_e=%s max_classes=%s, %d distinct colours. Picked: %s\n%s",
        delta_e,
        max_classes,
        len(cnt),
        picked,
        "\n".join(rows),
    )


def _log_counts(stage: str, lab: np.ndarray, region: np.ndarray, colours: np.ndarray) -> None:
    """Debug: pixels per label (base = 0, stains = 1..) after one step of the classifier."""
    counts = np.bincount(lab[lab >= 0], minlength=len(colours))
    parts = []
    for i in range(len(colours)):
        name = "base" if i == 0 else f"stain {i}"
        parts.append(f"{name} {_hex(colours[i])}={int(counts[i])}")
    logger.info(
        "classify [%s] %s | undefined=%d", stage, " | ".join(parts), int(((lab < 0) & region).sum())
    )


def _drop_border_bits(
    lab: np.ndarray,
    n_classes: int,
    near: np.ndarray,
    max_area: float,
    share: float,
    fill_region: np.ndarray,
) -> np.ndarray:
    """Remove the small pieces of a colour that lie (almost) entirely on a known border: a piece is
    dropped when it has at most `max_area` px and at least `share` of its pixels are in `near`.
    The biggest colour (the base map) is never touched. Dropped pixels take the nearest surviving label.
    """
    if nd_label is None or n_classes < 2 or not (lab >= 0).any():
        return lab
    counts = np.bincount(lab[lab >= 0], minlength=n_classes)
    base = int(counts.argmax())
    box8 = np.ones((3, 3), bool)
    out = lab.copy()
    changed = False
    for i in range(n_classes):
        if i == base or counts[i] == 0:
            continue
        comp, n = nd_label(lab == i, structure=box8)
        if n == 0:
            continue
        flat = comp.ravel()
        sizes = np.bincount(flat, minlength=n + 1)
        on_border = np.bincount(flat, weights=near.ravel(), minlength=n + 1)
        bad = (sizes <= max_area) & (on_border >= share * sizes)
        bad[0] = False
        if bad.any():
            out[np.isin(comp, np.flatnonzero(bad))] = -1
            changed = True
    return _fill_nearest(out, fill_region) if changed else lab


def _shifted(a: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """out[y, x] = a[y - dy, x - dx], -1 outside."""
    h, w = a.shape
    out = np.full_like(a, -1)
    out[max(dy, 0) : h + min(dy, 0), max(dx, 0) : w + min(dx, 0)] = a[
        max(-dy, 0) : h + min(-dy, 0), max(-dx, 0) : w + min(-dx, 0)
    ]
    return out


def _propagate(lab: np.ndarray, allowed: np.ndarray, iters: int) -> np.ndarray:
    """Give unlabelled pixels of `allowed` the label of a labelled neighbour, one pixel layer per
    iteration. Labels only travel through `allowed`, so nothing else changes.
    """
    lab = lab.copy()
    for _ in range(iters):
        todo = allowed & (lab < 0)
        if not todo.any():
            break
        new = lab.copy()
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            nb = _shifted(lab, dy, dx)
            take = todo & (new < 0) & (nb >= 0)
            new[take] = nb[take]
        lab = new
    return lab


def _fill_nearest(lab: np.ndarray, region: np.ndarray) -> np.ndarray:
    """Every pixel of `region` without a label takes the label of the nearest labelled pixel."""
    todo = region & (lab < 0)
    if not todo.any() or not (lab >= 0).any():
        return lab
    if distance_transform_edt is None:  # pragma: no cover
        return _propagate(lab, region, max(lab.shape))
    iy, ix = distance_transform_edt(lab < 0, return_distances=False, return_indices=True)
    out = lab.copy()
    out[todo] = lab[iy[todo], ix[todo]]
    return out


def _line_mask(geom, size: tuple[int, int]) -> np.ndarray:
    """Lines (or polygon outlines) -> boolean mask of `size` = (h, w), 1 px wide."""
    im = Image.new("L", (size[1], size[0]), 0)
    dr = ImageDraw.Draw(im)
    for ln in _line_parts(geom):
        dr.line([tuple(c) for c in ln.coords], fill=255, width=1)
    return np.asarray(im) > 0


def _raster(poly_geom, size: tuple[int, int]) -> np.ndarray:
    """Filled polygon(s) -> boolean mask of `size` = (h, w)."""
    im = Image.new("L", (size[1], size[0]), 0)
    dr = ImageDraw.Draw(im)
    polys = _polys(poly_geom)
    for p in polys:
        dr.polygon(shapely.get_coordinates(p.exterior).ravel().tolist(), fill=255)
    for p in polys:
        for ring in p.interiors:
            dr.polygon(shapely.get_coordinates(ring).ravel().tolist(), fill=0)
    return np.asarray(im) > 0


def _fill_small_holes(geoms: list, min_area: float) -> list:
    """Fill the small holes of each polygon (a label or border crossing a stain), except the
    part of a hole that belongs to another stain.
    """
    out = []
    for i, g in enumerate(geoms):
        fills = [
            shapely.Polygon(r)
            for p in _polys(g)
            for r in p.interiors
            if shapely.Polygon(r).area < min_area
        ]
        if not fills:
            out.append(g)
            continue
        fill = shapely.union_all(fills)
        for j, o in enumerate(geoms):
            if j != i:
                near = shapely.clip_by_rect(o, *fill.bounds)
                if not near.is_empty:
                    fill = shapely.difference(fill, near)
        out.append(shapely.union_all([g, fill]))
    return out


def _simplify_shared(geoms: list, tol: float) -> list:
    """Sub-pixel simplification that keeps neighbours glued (shared borders stay identical).
    Only done when the polygons are a valid coverage; otherwise the exact pixel outlines are kept.
    """
    if not geoms:
        return geoms
    arr = np.array(geoms, dtype=object)
    try:
        if shapely.coverage_is_valid(arr).all():
            out = shapely.coverage_simplify(arr, tol)
            bad = shapely.is_empty(out) | ~shapely.is_valid(out)
            return list(np.where(bad, arr, out))
    except Exception:  # noqa: BLE001
        pass
    return geoms


def _smooth_ring(ring: np.ndarray, sigma: float) -> np.ndarray:
    """Low-pass a closed ring along its length (resampled every ~sigma/2 px)."""
    p = np.asarray(ring, float)
    if len(p) < 5 or sigma <= 0.0:
        return p
    q = p[:-1] if np.allclose(p[0], p[-1]) else p
    seg = np.hypot(*(np.roll(q, -1, axis=0) - q).T)
    keep = seg > 1e-9
    if not keep.any():
        return p
    q = q[keep]
    seg = seg[keep]
    per = float(seg.sum())
    m = max(8, round(per / max(0.5, sigma / 2.0)))
    space = per / m
    s = np.arange(m) * space
    cum = np.concatenate([[0.0], np.cumsum(seg)])
    x = np.interp(s, cum, np.append(q[:, 0], q[0, 0]))
    y = np.interp(s, cum, np.append(q[:, 1], q[0, 1]))
    k = max(1, int(np.ceil(3 * sigma / space)))
    t = np.arange(-k, k + 1)
    w = np.exp(-0.5 * (t * space / sigma) ** 2)
    w /= w.sum()
    xs = np.convolve(np.pad(x, k, mode="wrap"), w, mode="valid")
    ys = np.convolve(np.pad(y, k, mode="wrap"), w, mode="valid")
    out = np.column_stack([xs, ys])
    return np.vstack([out, out[0]])


def _smooth_shared(geoms: list, sigma: float) -> list:
    """Smooth every ring of each polygon. A polygon that would become invalid keeps its outline."""
    out = []
    for g in geoms:
        parts = []
        for p in _polys(g):
            holes = [_smooth_ring(np.asarray(r.coords), sigma) for r in p.interiors]
            sp = shapely.Polygon(_smooth_ring(np.asarray(p.exterior.coords), sigma), holes)
            if sp.is_empty or not sp.is_valid:
                parts = []
                break
            parts.append(sp)
        out.append(shapely.union_all(parts) if parts else g)
    return out


# ── output helpers ─────────────────────────────────────────────────────────


def _path(geom, dx: float = 0.0, dy: float = 0.0) -> str:
    d = []
    for p in _polys(geom):
        for ring in (p.exterior, *p.interiors):
            c = np.asarray(ring.coords)
            d.append("M" + "L".join(f"{x + dx:.1f} {y + dy:.1f}" for x, y in c) + "Z")
    return "".join(d)


def _line_path(geom, dx: float = 0.0, dy: float = 0.0) -> str:
    d = []
    for g in shapely.get_parts(geom) if geom is not None and not geom.is_empty else []:
        if g.geom_type != "LineString":
            continue
        c = np.asarray(g.coords)
        d.append("M" + "L".join(f"{x + dx:.1f} {y + dy:.1f}" for x, y in c))
    return "".join(d)


def _match_out(m: dict | None) -> dict | None:
    """Rounded match numbers for the API.

    precision: share of line samples within `tol` px of an image edge that runs the same way (0..1).
    median / p90: distance (px) of those samples to that edge.
    chance_median / chance_precision: the same measures with the line shifted a few px.
    reverse: share of the strong image edges next to the line that the line explains (0..1).
    """
    if m is None:
        return None

    def rnd(key: str, nd: int):
        v = m.get(key)
        return None if v is None else round(v, nd)

    return {
        "precision": round(m["precision"], 3),
        "median": round(m["median"], 2),
        "p90": round(m["p90"], 2),
        "tol": m["tol"],
        "chance_median": rnd("chance_median", 2),
        "chance_precision": rnd("chance_precision", 3),
        "reverse": rnd("reverse", 3),
    }


def _region_stats(geom, dt: np.ndarray | None) -> dict | None:
    """Edge match split by quadrant of the placed outline (NW / NE / SW / SE).

    A single percentage can hide one side of the country being completely off. The split is
    only a diagnostic: it does not change the registration.
    """
    if dt is None or geom is None or geom.is_empty:
        return None
    pts = shapely.get_coordinates(shapely.segmentize(geom, MATCH_STEP_PX))
    if not len(pts):
        return None
    hh, ww = dt.shape
    xs = np.rint(pts[:, 0] - 0.5).astype(int)
    ys = np.rint(pts[:, 1] - 0.5).astype(int)
    ok = (xs >= 0) & (xs < ww) & (ys >= 0) & (ys < hh)
    if ok.sum() < 10:
        return None
    x, y = pts[ok, 0], pts[ok, 1]
    d = dt[ys[ok], xs[ok]]
    bx0, by0, bx1, by1 = geom.bounds
    cx, cy = (bx0 + bx1) / 2, (by0 + by1) / 2
    out = {}
    for name, mask in (
        ("NW", (x < cx) & (y < cy)),
        ("NE", (x >= cx) & (y < cy)),
        ("SW", (x < cx) & (y >= cy)),
        ("SE", (x >= cx) & (y >= cy)),
    ):
        if int(mask.sum()) < 5:
            out[name] = None
        else:
            out[name] = {
                "precision": round(float(np.mean(d[mask] <= MATCH_TOL_PX)), 3),
                "median": round(float(np.median(d[mask])), 2),
                "samples": int(mask.sum()),
            }
    return out


def _warp_geom(
    geom, x0: int, y0: int, sx: float, sy: float, dx: float, dy: float, pivot: list[float]
):
    """Scale around `pivot` (work px) then translate, keeping the result crop-local."""
    if geom is None or geom.is_empty:
        return geom
    px, py = float(pivot[0]), float(pivot[1])
    work = affine_transform(geom, [1, 0, 0, 1, x0, y0])
    m = [sx, 0, 0, sy, px + dx - sx * px, py + dy - sy * py]
    return affine_transform(affine_transform(work, m), [1, 0, 0, 1, -x0, -y0])


def evaluate_warp(
    arr: np.ndarray,
    outline,
    borders,
    crop: list[float] | None,
    projection: str,
    cache: dict | None,
    sx: float,
    sy: float,
    dx: float,
    dy: float,
    pivot: list[float],
) -> dict:
    """Score a manual warp of the fitted outline and borders (no stains are recomputed).

    The warp is applied on top of the fitted affine: an extra independent X/Y scale plus
    translation. `pivot` and the offsets are in work-image px.
    """
    h0, w0 = arr.shape[:2]
    fx0, fy0, fx1, fy1 = crop or (0.0, 0.0, 1.0, 1.0)
    x0 = int(np.clip(round(fx0 * w0), 0, max(0, w0 - MIN_CROP)))
    x1 = int(np.clip(round(fx1 * w0), min(w0, x0 + MIN_CROP), w0))
    y0 = int(np.clip(round(fy0 * h0), 0, max(0, h0 - MIN_CROP)))
    y1 = int(np.clip(round(fy1 * h0), min(h0, y0 + MIN_CROP), h0))
    sub = arr[y0:y1, x0:x1]

    key = (x0, y0, x1, y1, projection)
    reg = cache.get(key) if cache is not None else None
    if reg is None:
        reg = register(sub, outline, borders, projection)
        if cache is not None:
            cache[key] = reg

    placed = _warp_geom(reg["placed"], x0, y0, sx, sy, dx, dy, pivot)
    placed_b = None
    if reg["placed_b"] is not None:
        placed_b = _warp_geom(reg["placed_b"], x0, y0, sx, sy, dx, dy, pivot)
    dt = reg.get("dt")
    m_o = _line_match(placed, dt)
    m_b = _line_match(placed_b, dt) if placed_b is not None else None
    bx0, by0, bx1, by1 = placed.bounds
    amat = np.asarray(reg["A"], float)
    len_x = float(np.hypot(*amat[:, 0]))
    len_y = float(np.hypot(*amat[:, 1]))
    return {
        "fit": {
            "outline": 0.0 if m_o is None else round(m_o["precision"], 3),
            "borders": None if m_b is None else round(m_b["precision"], 3),
            "outline_match": _match_out(m_o),
            "borders_match": _match_out(m_b),
            "regions": _region_stats(placed, dt),
            "projection": reg["name"],
            "aspect": round(sx * len_x / ((sy * len_y) or 1.0), 3),
            "box": [x0 + bx0, y0 + by0, x0 + bx1, y0 + by1],
        },
        "warp": {
            "sx": round(float(sx), 4),
            "sy": round(float(sy), 4),
            "dx": round(float(dx), 2),
            "dy": round(float(dy), 2),
            "pivot": [round(float(pivot[0]), 2), round(float(pivot[1]), 2)],
        },
    }


def _mask_png(mask: np.ndarray, size: tuple[int, int], offset: tuple[int, int], rgb) -> str:
    """Boolean crop-local mask -> transparent PNG (data URL) the size of the work image."""
    h0, w0 = size
    oy, ox = offset
    out = np.zeros((h0, w0, 4), np.uint8)
    out[oy : oy + mask.shape[0], ox : ox + mask.shape[1]][mask] = (*rgb, 255)
    buf = io.BytesIO()
    Image.fromarray(out, "RGBA").save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


# ── main entry ─────────────────────────────────────────────────────────────


def analyze(
    arr: np.ndarray,
    full: np.ndarray,
    outline,
    crop: list[float] | None = None,
    projection: str = "auto",
    borders=None,
    cache: dict | None = None,
    hint: list[float] | None = None,
    debug: bool = False,
    params: dict | None = None,
) -> dict:
    """arr: work-size RGB (used to find the map). full: the same image at full resolution (used to
    read the stains). outline: the country's lon/lat geometry. crop: fractions [x0, y0, x1, y1]
    that limit where the map is searched (None = whole image). borders: lon/lat lines of the
    first-level admin borders. cache: registrations are stored here (they are the slow part).
    hint: fractions [x0, y0, x1, y1] of the image where a map was detected (a rough guess that
    biases the search; may be too small or off).
    debug: also return the edge pixels the final fit looks at (see "debug" in the result).
    params: the three stain settings: delta_e, max_classes, expansion_px (see StainParams in the API).

    Every colour found inside the map is returned (clipped to the outline), the base map flagged
    `base`. The app's own country map is drawn underneath, so the UI leaves the base unselected.
    """
    tune = params or {}
    delta_e = float(tune.get("delta_e", DELTA_E))
    max_classes = int(tune.get("max_classes", MAX_CLASSES))
    expand_px = int(tune.get("expansion_px", EXPAND_PX))
    h0, w0 = arr.shape[:2]
    fx0, fy0, fx1, fy1 = crop or (0.0, 0.0, 1.0, 1.0)
    x0 = int(np.clip(round(fx0 * w0), 0, max(0, w0 - MIN_CROP)))
    x1 = int(np.clip(round(fx1 * w0), min(w0, x0 + MIN_CROP), w0))
    y0 = int(np.clip(round(fy0 * h0), 0, max(0, h0 - MIN_CROP)))
    y1 = int(np.clip(round(fy1 * h0), min(h0, y0 + MIN_CROP), h0))
    sub = arr[y0:y1, x0:x1]

    # the hint in crop-local work px (it stays the same for the life of an upload, so it is part of no cache key)
    hint_px = None
    if hint is not None:
        hint_px = (hint[0] * w0 - x0, hint[1] * h0 - y0, hint[2] * w0 - x0, hint[3] * h0 - y0)

    # 1. where is the map (on the work image), and which pixel is which lon/lat
    key = (x0, y0, x1, y1, projection)
    reg = cache.get(key) if cache is not None else None
    if reg is None:
        reg = register(sub, outline, borders, projection, hint_px)
        if cache is not None:
            cache[key] = reg
    amat, tvec = reg["A"], reg["t"]
    ainv_t = np.linalg.inv(amat).T
    inv = reg["inv"]

    # 2. move everything to the full-resolution crop
    big_h, big_w = full.shape[:2]
    kx, ky = big_w / w0, big_h / h0
    scale = max(1.0, (kx + ky) / 2)  # how much bigger the full image is than the work one
    fx_0 = min(big_w - 1, round(x0 * kx))
    fx_1 = max(fx_0 + 1, min(big_w, round(x1 * kx)))
    fy_0 = min(big_h - 1, round(y0 * ky))
    fy_1 = max(fy_0 + 1, min(big_h, round(y1 * ky)))
    img = full[fy_0:fy_1, fx_0:fx_1]
    hgt, wid = img.shape[:2]
    to_full = [kx, 0, 0, ky, x0 * kx - fx_0, y0 * ky - fy_0]
    placed = affine_transform(reg["placed"], to_full)

    def to_lonlat(xy):
        sx = (xy[:, 0] + fx_0) / kx - x0  # back to crop-local work-image px
        sy = (xy[:, 1] + fy_0) / ky - y0
        uv = (np.column_stack([sx, sy]) - tvec) @ ainv_t  # undo px = A @ uv + t
        return inv(uv)

    def rad(n: int) -> int:  # a radius that is n px on the work image, in full-res px
        return max(0, round(n * scale))

    inside_main = _raster(placed, (hgt, wid))
    if inside_main.sum() < 100:
        msg = "The map was placed outside the crop. Try a bigger crop."
        raise StainError(msg)
    # The fit outline leaves out the small islands (Tasmania, Tierra del Fuego...). They are added here so a
    # stain on them is read too.
    inside = inside_main
    if reg.get("placed_extra") is not None:
        extra = affine_transform(reg["placed_extra"], to_full)
        inside = inside_main | _raster(extra, (hgt, wid))
    # Only the pixels inside the map outline (SCOPE_PX beyond it) are classified.
    region = _dilate(inside, SCOPE_PX)

    # 3. ONE label per pixel: base map = 0, stains = 1..  (see _classify)
    lab, centers = _classify(
        img, region, delta_e, max_classes, expand_px, rad(BORDER_INSET_PX), HOLE_MIN * scale * scale
    )

    # the app's own country outline, a few px wider: the drawn coast and the fitted one never agree exactly
    p0, p1 = to_lonlat(np.array([[wid / 2, hgt / 2], [wid / 2 + 1, hgt / 2]], float))
    clip = reg["clip_outline"].buffer(rad(CLIP_PX) * float(np.hypot(*(p1 - p0))))

    # 4. polygons straight from the label map: disjoint pixels -> disjoint polygons
    counts = np.bincount(lab[lab >= 0], minlength=max(1, len(centers)))
    geoms, ids = [], []
    for i in np.argsort(-counts, kind="stable"):
        if counts[i] <= 0:
            continue
        parts = [p for p in _polys(_runs_geom(lab == i)) if p.area >= SMALL_PIECE_PX]
        if parts:
            geoms.append(shapely.union_all(parts))
            ids.append(int(i))
    geoms = _fill_small_holes(geoms, HOLE_MIN * scale * scale)
    geoms = _simplify_shared(geoms, SIMPLIFY_PX)
    geoms = _smooth_shared(geoms, SMOOTH_PX)

    classes, features = [], []
    total = sum(int(counts[i]) for i in ids) or 1
    stain_n = 0
    for g, i in zip(geoms, ids, strict=True):
        ll = shapely.make_valid(shapely.transform(g, to_lonlat))
        ll = _poly_only(shapely.intersection(ll, clip))
        if ll.is_empty:
            continue
        try:
            ll = shapely.set_precision(ll, 1e-6)
        except Exception:  # noqa: BLE001
            pass
        hx = _hex(centers[i])
        is_base = i == 0  # label 0 is always the base map
        if is_base:
            tag = "Base"
        else:
            stain_n += 1
            tag = f"Stain {stain_n}"
        classes.append(
            {"hex": hx, "tag": tag, "share": round(int(counts[i]) / total, 4), "base": is_base}
        )
        features.append(
            {
                "type": "Feature",
                "properties": {"tag": tag, "color": hx, "hex": hx, "base": is_base},
                "geometry": json.loads(shapely.to_geojson(ll)),
            }
        )

    bx0, by0, bx1, by1 = reg["placed"].bounds
    padx, pady = 0.03 * (bx1 - bx0), 0.03 * (by1 - by0)
    col_x, col_y = amat[:, 0], amat[:, 1]  # where the plane's x / y axes go in the image
    len_x, len_y = float(np.hypot(*col_x)), float(np.hypot(*col_y))
    skew = math.degrees(
        math.acos(max(-1.0, min(1.0, float(col_x @ col_y) / ((len_x * len_y) or 1.0))))
    )
    return {
        "size": [w0, h0],
        "bbox": [
            max(0.0, (x0 + bx0 - padx) / w0),
            max(0.0, (y0 + by0 - pady) / h0),
            min(1.0, (x0 + bx1 + padx) / w0),
            min(1.0, (y0 + by1 + pady) / h0),
        ],
        "fit": {
            # `confidence` is the chance-corrected outline match: how much better the line sits
            # on matching edges than the same line shifted a few px. Only used for the verdict.
            "confidence": round(float(max(0.0, min(1.0, reg["lift"]))), 3),
            "outline": round(reg["hit_o"], 3),
            "borders": None if reg["hit_b"] is None else round(reg["hit_b"], 3),
            # The real match: share of the line within `tol` px of an edge running the same way,
            # with median / p90 distance, the shifted-line chance level and the reverse check.
            # None when it can't be measured (no scipy).
            "outline_match": _match_out(reg.get("m_o")),
            "borders_match": _match_out(reg.get("m_b")),
            "regions": _region_stats(reg["placed"], reg.get("dt")),
            "projection": reg["name"],
            "aspect": round(len_x / (len_y or 1.0), 3),
            # The placed outline in work-image px: pivot for the manual warp.
            "box": [x0 + bx0, y0 + by0, x0 + bx1, y0 + by1],
            # How well the chosen placement agrees with the detected map box (1 = fully, None = no hint).
            "hint_prior": None if hint is None else round(float(reg.get("prior", 1.0)), 3),
            "rotation_deg": round(math.degrees(math.atan2(amat[1, 0], amat[0, 0])), 2),
            "skew_deg": round(skew - 90.0, 2),
        },
        "classes": classes,
        # The base map for the preview: the country's own outline and borders (never from the image).
        # (The API replaces `outline` with the one simplified together with the borders.)
        "base": {
            "outline": json.loads(shapely.to_geojson(reg["clip_outline"])),
            "borders": None
            if borders is None or borders.is_empty
            else json.loads(shapely.to_geojson(borders)),
        },
        "geojson": {"type": "FeatureCollection", "features": features},
        # Debug view: the strong edges (Lab gradient) the final fit sees, as a transparent PNG the size
        # of the work image.
        "debug": None
        if not debug
        else {
            "edges": _mask_png(_edge_field(sub)[0] >= STRONG_W, (h0, w0), (y0, x0), (0, 229, 255)),
        },
        "overlay": {
            "outline": _path(reg["placed"], x0, y0),
            "borders": _line_path(reg["placed_b"], x0, y0),
        },
    }
