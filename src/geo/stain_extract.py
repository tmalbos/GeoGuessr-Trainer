"""stain_extract.py — Turn a screenshot of a country map with painted areas ("stains") into polygons.

Every colour found inside the map is returned as a polygon, with the biggest one flagged `base`.
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
  4. colours       everything below runs on the FULL-resolution image, pixel by pixel. There is no
                   "base colour" guess and no "is this pixel different enough" test: the colours of
                   the map are the peaks of the FLAT pixels inside the placed outline (at most
                   MAX_CLASSES; borders, text and anti-aliasing are not flat, so they never become a
                   colour). The most common colour is simply the base map: it is returned too, as a
                   stain flagged `base`, for the UI to leave unselected.
  4b. labels       ONE label map covering the whole inside of the map: every pixel close to a colour
                   takes the nearest one, thin things (borders, labels, rims) and tiny blobs are
                   removed, and every pixel left without a label takes the label of the nearest
                   labelled pixel. A pixel has exactly one label, so stains can never overlap and
                   nothing inside the map is left "unclassified".
  5. polygons      each label -> polygon straight from its pixels, so neighbouring stains share the
                   exact same boundary. No blur, no per-stain growing: the only change is an
                   optional gap-free simplification (sub-pixel tolerance) when the polygons form a
                   valid coverage. Then lon/lat, clipped to the outline.

The registration is a 2x3 affine in the projected plane: px = A @ (u, v) + t. A is a full 2x2 matrix
(it may rotate / shear), so every consumer uses A and t, never separate scale factors.
"""

import io
import json
import math

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

try:  # optimiser of the affine polish
    from scipy.optimize import minimize
except ImportError:  # pragma: no cover
    minimize = None

WORK_MAX = 1600  # longest side of the image used to find the map, px (near native for a screenshot)
FULL_MAX = 2400  # longest side of the image the stains are read from, px
TOL = 40  # L1 colour distance (sum over R,G,B) under which two shades are merged into one stain
MIN_CROP = 32  # smallest crop side (in work px)
MIN_CLASS_SHARE = 0.0015  # a stain smaller than this share of the map is ignored
EDGE_T = 40.0  # gradient magnitude (sum over channels) that counts as an edge (coarse search / ICP)
LIGHT_MIN = 170  # a pixel is "light" when its darkest channel is at least this
COARSE_MAX = 300  # longest side of the image used by the coarse search
SCALE_STEP = 1.05  # ratio between two scales of the coarse search
MIN_MAP_FRACTION = 0.2  # smallest map considered, relative to the largest that fits the image
TOP_K = 3  # coarse candidates that get refined
ICP_ITERS = 45
CENTER_SAMPLE = 300_000  # pixels used to find the map colours (the assignment uses every pixel)
SIMPLIFY_PX = 0.7  # gap-free simplification tolerance, in full-res px (sub-pixel)

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
MAX_CLASSES = 7  # colours kept inside the map (base map included)
FLAT_T = 24.0  # gradient magnitude (sum over channels) under which a pixel is "flat"
CAP = 1.5  # a pixel takes a colour only within CAP * tol of it; the rest is filled from neighbours
HOLE_MIN = 30.0  # holes in a stain smaller than this (px² at work size) are filled
MIN_POLY = 8.0  # polygons smaller than this (px² at work size) are dropped


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
        if w <= ww and h <= hh:
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
    for k in range(N_BINS):
        compat = np.maximum(0.0, np.cos(2 * (phi - k * step))) ** ALIGN_POWER
        out[k] = gaussian_filter(strength * compat, sigma, mode="constant") * norm
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
    pts: list[np.ndarray] = []
    tans: list[np.ndarray] = []
    if geom is not None:
        for ln in _line_parts(geom):
            length = ln.length
            if length < spacing * 3:
                continue
            closed = ln.is_closed
            n = max(8, int(length / spacing))
            d = np.linspace(0.0, length, n, endpoint=not closed)

            def at(dd, ln=ln, length=length, closed=closed):
                dd = dd % length if closed else np.clip(dd, 0.0, length)
                return shapely.get_coordinates(shapely.line_interpolate_point(ln, dd))

            p, a, b = at(d), at(d - spacing), at(d + spacing)
            t = b - a
            nrm = np.hypot(t[:, 0], t[:, 1])
            ok = nrm > 1e-12
            pts.append(p[ok])
            tans.append(t[ok] / nrm[ok, None])
    if not pts:
        return np.empty((0, 2)), np.empty((0, 2))
    return np.vstack(pts), np.vstack(tans)


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

    def at(xi, yi):
        ok = (xi >= 0) & (xi < w) & (yi >= 0) & (yi < h)
        out = np.zeros(len(xi), np.float32)
        out[ok] = f[k[ok], yi[ok], xi[ok]]
        return out

    return (
        at(x0, y0) * (1 - ax) * (1 - ay)
        + at(x0 + 1, y0) * ax * (1 - ay)
        + at(x0, y0 + 1) * (1 - ax) * ay
        + at(x0 + 1, y0 + 1) * ax * ay
    )


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
    dist: dict[int, np.ndarray] = {}
    for kk in np.unique(k):
        m = strong & (np.cos(2 * (phi - int(kk) * step)) >= ALIGN_COS)
        dist[int(kk)] = (
            distance_transform_edt(~m).astype(np.float32)
            if m.any()
            else np.full((h, w), 99.0, np.float32)
        )

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
    parts = _polys(shapely.make_valid(outline))
    if not parts:
        msg = "That country has no outline to match against"
        raise StainError(msg)
    big = max(p.area for p in parts)
    fit = shapely.simplify(shapely.union_all([p for p in parts if p.area >= 0.02 * big]), 0.01)
    clip = shapely.make_valid(shapely.simplify(shapely.union_all(parts), 0.003))
    return fit, clip


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
    fit_outline, clip_outline = _prep_outline(outline)
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
            {**c, "name": nm, "uv": uv, "uvb": uvb, "inv": inv}
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
        r["prior"] = float(_hint_prior(bx0, by0, bw, bh, hint))
        refined.append({**r, "name": c["name"], "uv": c["uv"], "uvb": c["uvb"], "inv": c["inv"]})

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
        "clip_outline": clip_outline,
        "fit_outline": fit_outline,
        "dt": dt,
    }


# ── stains (inside the matched shape only, full resolution) ────────────────


def _find_centers(pix: np.ndarray, tol: int = TOL, max_k: int = 14) -> np.ndarray:
    """Peak-based colour clustering of (N, 3) pixels. Returns the map colours, (K, 3)."""
    pix = pix.astype(np.int16)
    centers: list[np.ndarray] = []
    idx = np.arange(len(pix))
    min_px = max(20, int(MIN_CLASS_SHARE * len(pix) * 0.5))
    for _ in range(max_k):
        if len(idx) < min_px:
            break
        sub = pix[idx]
        key = ((sub[:, 0] >> 4) << 8) | ((sub[:, 1] >> 4) << 4) | (sub[:, 2] >> 4)
        cnt = np.bincount(key, minlength=4096)
        peak = int(cnt.argmax())
        if cnt[peak] < min_px:
            break
        c = sub[key == peak].mean(0)
        take = np.abs(sub - c).sum(1) <= tol
        c = sub[take].mean(0)
        take = np.abs(sub - c).sum(1) <= tol
        centers.append(c)
        idx = idx[~take]
    return np.array(centers).reshape(-1, 3)


def _assign(img: np.ndarray, region: np.ndarray, centers, cap: float) -> np.ndarray:
    """ONE label per pixel of `region` (-1 = none). Every pixel goes to its nearest colour, as long
    as it is within `cap` of it (L1 over R, G, B): the edge between two colours falls halfway
    between them, so anti-aliased pixels are split fairly. A pixel far from every colour (text,
    lines, photo clutter) gets no label here and is filled from its neighbours later.
    """
    h, w = region.shape
    lab = np.full(h * w, -1, np.int16)
    idx = np.flatnonzero(region.ravel())
    if not len(centers) or not len(idx):
        return lab.reshape(h, w)
    pix = img.reshape(-1, 3)[idx].astype(np.int16)
    ci = np.rint(centers).astype(np.int16)
    best = np.full(len(idx), np.iinfo(np.int32).max, np.int32)
    which = np.zeros(len(idx), np.int16)
    for k, c in enumerate(ci):
        d = np.abs(pix - c).sum(1, dtype=np.int32)
        better = d < best
        best[better] = d[better]
        which[better] = k
    ok = best <= cap
    lab[idx[ok]] = which[ok]
    return lab.reshape(h, w)


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


def _raster(poly_geom, size: tuple[int, int]) -> np.ndarray:
    """Filled polygon(s) -> boolean mask of `size` = (h, w)."""
    im = Image.new("L", (size[1], size[0]), 0)
    dr = ImageDraw.Draw(im)
    polys = _polys(poly_geom)
    for p in polys:
        dr.polygon([tuple(c) for c in p.exterior.coords], fill=255)
    for p in polys:
        for ring in p.interiors:
            dr.polygon([tuple(c) for c in ring.coords], fill=0)
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


# ── main entry ─────────────────────────────────────────────────────────────


def analyze(
    arr: np.ndarray,
    full: np.ndarray,
    outline,
    crop: list[float] | None = None,
    projection: str = "auto",
    tol: int = TOL,
    borders=None,
    cache: dict | None = None,
    hint: list[float] | None = None,
) -> dict:
    """arr: work-size RGB (used to find the map). full: the same image at full resolution (used to
    read the stains). outline: the country's lon/lat geometry. crop: fractions [x0, y0, x1, y1]
    that limit where the map is searched (None = whole image). borders: lon/lat lines of the
    first-level admin borders. cache: registrations are stored here (they are the slow part).
    hint: fractions [x0, y0, x1, y1] of the image where a map was detected (a rough guess that
    biases the search; may be too small or off).

    Every colour found inside the map is returned (clipped to the outline), the biggest one flagged
    `base`. The app's own country map is drawn underneath, so the UI leaves the base unselected.
    """
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
    placed = affine_transform(reg["placed"], [kx, 0, 0, ky, x0 * kx - fx_0, y0 * ky - fy_0])

    def to_lonlat(xy):
        sx = (xy[:, 0] + fx_0) / kx - x0  # back to crop-local work-image px
        sy = (xy[:, 1] + fy_0) / ky - y0
        uv = (np.column_stack([sx, sy]) - tvec) @ ainv_t  # undo px = A @ uv + t
        return inv(uv)

    def rad(n: int) -> int:  # a radius that is n px on the work image, in full-res px
        return max(1, round(n * scale))

    r_edge, r_thin = rad(2), rad(1)

    inside = _raster(placed, (hgt, wid))
    if inside.sum() < 100:
        msg = "The map was placed outside the crop. Try a bigger crop."
        raise StainError(msg)
    core = _erode(inside, r_edge)  # away from the drawn coast line
    if core.sum() < 100:
        msg = "The map was placed outside the crop. Try a bigger crop."
        raise StainError(msg)
    n_core = int(core.sum())

    # 3. the colours of the map: peaks of the FLAT pixels inside it (the base map is one of them)
    flat = _open((_grad(img) <= FLAT_T) & core, r_thin)  # borders, text and rims are not flat
    pool = img[flat] if flat.sum() >= 100 else img[core]
    centers = _find_centers(_subsample(pool, CENTER_SAMPLE), tol, MAX_CLASSES)
    if not len(centers):
        msg = "No colours were found inside the map."
        raise StainError(msg)

    # 3b. ONE label per pixel, over the whole inside of the map
    lab = _assign(img, core, centers, CAP * tol)
    raw = lab.copy()
    min_blob = max(15 * scale * scale, MIN_CLASS_SHARE * n_core)
    for k in range(len(centers)):
        # thin parts (borders, labels, rims) and tiny blobs are not a colour
        own = lab == k
        m = _open(own, r_thin)
        lab[own & ~m] = -1
        if m.sum() < min_blob:
            lab[own] = -1
    if not (lab >= 0).any():  # nothing survived: keep the raw labels rather than return nothing
        lab = raw
    lab = _fill_nearest(lab, inside)  # whatever is still unlabelled takes its nearest neighbour

    # 4. polygons straight from the label map: disjoint pixels -> disjoint polygons
    counts = np.bincount(lab[lab >= 0], minlength=max(1, len(centers)))
    geoms, ids = [], []
    for i in np.argsort(-counts, kind="stable"):
        if counts[i] <= 0:
            continue
        parts = [p for p in _polys(_runs_geom(lab == i)) if p.area >= MIN_POLY * scale * scale]
        if parts:
            geoms.append(shapely.union_all(parts))
            ids.append(int(i))
    geoms = _fill_small_holes(geoms, HOLE_MIN * scale * scale)
    geoms = _simplify_shared(geoms, SIMPLIFY_PX)

    classes, features = [], []
    total = sum(int(counts[i]) for i in ids) or 1
    base_id = ids[0] if ids else -1  # ids are sorted by size: the biggest colour is the base map
    stain_n = 0
    for g, i in zip(geoms, ids, strict=True):
        ll = shapely.make_valid(shapely.transform(g, to_lonlat))
        ll = _poly_only(shapely.intersection(ll, reg["clip_outline"]))
        if ll.is_empty:
            continue
        try:
            ll = shapely.set_precision(ll, 1e-6)
        except Exception:  # noqa: BLE001
            pass
        hx = _hex(centers[i])
        is_base = i == base_id
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
        "base": {
            "outline": json.loads(shapely.to_geojson(shapely.simplify(reg["clip_outline"], 0.005))),
            "borders": None
            if borders is None or borders.is_empty
            else json.loads(shapely.to_geojson(borders)),
        },
        "geojson": {"type": "FeatureCollection", "features": features},
        "overlay": {
            "outline": _path(reg["placed"], x0, y0),
            "borders": _line_path(reg["placed_b"], x0, y0),
        },
    }
