"""stain_extract.py — Turn a screenshot of a country map with painted areas ("stains") into polygons.

The base map is NEVER extracted: the app already has the country's own map and draws it underneath.
Only the stains (painted areas) are returned, as polygons in lon/lat.

The map is found by REGISTRATION, not by guessing from colours. The country is given, so its
outline and first-level borders are known:

  1. _edges        crisp edges of the image, kept only next to LIGHT pixels (a drawn map sits on a
                   light base; trees, roofs and shadows of a photo mostly don't).
  2. _candidates   coarse search: slide the known outline + borders over the edge map at several
                   scales, per projection. The score is "line pixels that land on an edge, above
                   what chance gives" times "how light the inside of the placed outline is".
  3. _refine       the best few placements are refined with annealed ICP against the edge pixels.
                   (Steps 1-3 run on a SMALL copy of the image: they only need to find the placement.)
  4. stains        everything below runs on the FULL-resolution image, pixel by pixel:
                     - the base colour is the most common flat colour inside the matched shape;
                     - a pixel is a stain candidate when it differs from it in CONTRAST (Lab,
                       chroma-aware), except bluish pixels (lakes / rivers);
                     - the stain colours are found by peak clustering;
                     - ONE label map is built: each candidate pixel goes to the nearest stain colour
                       (the base colour competes too, so the edge falls halfway through anti-aliased
                       pixels). A pixel has exactly one label, so stains can never overlap;
                     - thin things (borders, labels) are removed from the label map, and the thin
                       gaps they leave are filled from the neighbouring labels.
  5. polygons      each label -> polygon straight from its pixels, so neighbouring stains share the
                   exact same boundary. No blur, no per-stain growing: the only change is an
                   optional gap-free simplification (sub-pixel tolerance) when the polygons form a
                   valid coverage. Then lon/lat, clipped to the outline.
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

WORK_MAX = 800  # longest side of the SMALL image used to find the map, px
FULL_MAX = 2400  # longest side of the image the stains are read from, px
TOL = 40  # L1 colour distance (sum over R,G,B) under which two shades are merged into one stain
MIN_CROP = 32  # smallest crop side (in work px)
MIN_CLASS_SHARE = 0.0015  # a stain smaller than this share of the map is ignored
EDGE_T = 40.0  # gradient magnitude (sum over channels) that counts as an edge
LIGHT_MIN = 170  # a pixel is "light" when its darkest channel is at least this
COARSE_MAX = 300  # longest side of the image used by the coarse search
SCALE_STEP = 1.05  # ratio between two scales of the coarse search
MIN_MAP_FRACTION = 0.2  # smallest map considered, relative to the largest that fits the image
TOP_K = 3  # coarse candidates that get refined
ICP_ITERS = 45
CENTER_SAMPLE = 300_000  # pixels used to find the stain colours (the assignment uses every pixel)
SIMPLIFY_PX = 0.7  # gap-free simplification tolerance, in full-res px (sub-pixel)

# Stain contrast (CIE Lab, distance to the base colour)
AB_MIN = 8.0  # colour (chroma) difference needed...
DE_MIN = 9.0  # ...together with this much overall difference
DE_STRONG = 28.0  # or this much overall difference alone (dark / very different fills)
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


def _close(m: np.ndarray, r: int) -> np.ndarray:
    return _erode(_dilate(m, r), r)


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


def _edges(a: np.ndarray) -> np.ndarray:
    """Boolean edge map: central-difference gradient magnitude over all channels."""
    f = a.astype(np.float32)
    gx = np.zeros_like(f)
    gy = np.zeros_like(f)
    gx[:, 1:-1] = f[:, 2:] - f[:, :-2]
    gy[1:-1] = f[2:] - f[:-2]
    return np.sqrt((gx**2 + gy**2).sum(2)) > EDGE_T


def _fast_len(n: int) -> int:
    while True:
        m = n
        for p in (2, 3, 5):
            while m % p == 0:
                m //= p
        if m == 1:
            return n
        n += 1


# ── coarse search ──────────────────────────────────────────────────────────


def _candidates(e_coarse: np.ndarray, light_coarse: np.ndarray, f: int, uv, uvb) -> list[dict]:
    """Best placement of the known lines at every scale. Returns [{score, a, tx, ty}] in FULL-res
    px for x = a*u + tx, y = -a*v + ty.

    score = (line pixels on edges, above chance) x (how light the inside of the outline is).
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
    """Annealed ICP of the outline (+ border) samples onto the edge pixels. x = a*u+tx, y = b*v+ty."""
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


def _prep_outline(outline):
    parts = _polys(shapely.make_valid(outline))
    if not parts:
        msg = "That country has no outline to match against"
        raise StainError(msg)
    big = max(p.area for p in parts)
    fit = shapely.simplify(shapely.union_all([p for p in parts if p.area >= 0.02 * big]), 0.01)
    clip = shapely.make_valid(shapely.simplify(shapely.union_all(parts), 0.003))
    return fit, clip


def register(sub: np.ndarray, outline, borders, projection: str) -> dict:
    """Find where the country is in `sub` and the plane <-> image transform. Crop-local.

    The coarse search runs for every projection that makes sense for the country's bounds
    (equirectangular, plain lon/lat, mercator, and the two conics for mid-latitude countries);
    the best placement wins.
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
    light = sub.min(2) >= LIGHT_MIN
    e = _edges(sub)
    near_light = e & _dilate(light, 2)
    if near_light.sum() >= 200:
        e = near_light
    e_dil = _dilate(e, 2)
    f = max(1, math.ceil(max(hgt, wid) / COARSE_MAX))
    hc, wc = hgt // f, wid // f
    e_coarse = e[: hc * f, : wc * f].reshape(hc, f, wc, f).max((1, 3))
    light_coarse = light[: hc * f, : wc * f].reshape(hc, f, wc, f).mean((1, 3)) >= 0.5

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
            for c in _candidates(e_coarse, light_coarse, f, uv, uvb)
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

    best = None
    for c in chosen:
        r = _refine(e, e_dil, c["uv"], c["uvb"], c)
        if best is None or r["lift"] > best["lift"]:
            best = {**r, "name": c["name"], "uv": c["uv"], "uvb": c["uvb"], "inv": c["inv"]}
    m = [best["a"], 0, 0, best["b"], best["tx"], best["ty"]]
    return {
        **{k: best[k] for k in ("a", "b", "tx", "ty", "hit_o", "hit_b", "lift", "name")},
        "inv": best["inv"],
        "placed": affine_transform(best["uv"], m),
        "placed_b": affine_transform(best["uvb"], m) if best["uvb"] is not None else None,
        "clip_outline": clip_outline,
        "fit_outline": fit_outline,
    }


# ── stains (inside the matched shape only, full resolution) ────────────────


def _find_centers(pix: np.ndarray, tol: int = TOL, max_k: int = 14) -> np.ndarray:
    """Peak-based colour clustering of (N, 3) pixels. Returns the stain colours, (K, 3)."""
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


def _assign(img: np.ndarray, cand: np.ndarray, centers, base_rgb, tol: int) -> np.ndarray:
    """ONE label per pixel (-1 = none). Every candidate pixel goes to its nearest stain colour,
    with the base colour competing: the edge between a stain and the base (or between two stains)
    falls halfway between their colours, so anti-aliased pixels are split fairly. A pixel that is
    far from every stain colour (text, lines, photo clutter) gets no label.
    """
    h, w = cand.shape
    lab = np.full(h * w, -1, np.int16)
    idx = np.flatnonzero(cand.ravel())
    if not len(centers) or not len(idx):
        return lab.reshape(h, w)
    pix = img.reshape(-1, 3)[idx].astype(np.int16)
    ci = np.rint(centers).astype(np.int16)
    bi = np.rint(base_rgb).astype(np.int16)
    best = np.full(len(idx), np.iinfo(np.int32).max, np.int32)
    which = np.zeros(len(idx), np.int16)
    for k, c in enumerate(ci):
        d = np.abs(pix - c).sum(1, dtype=np.int32)
        better = d < best
        best[better] = d[better]
        which[better] = k
    d_base = np.abs(pix - bi).sum(1, dtype=np.int32)
    cap = np.maximum(tol, 0.6 * np.abs(ci - bi).sum(1))  # how far from its colour a pixel may be
    ok = (best <= cap[which]) & (best < d_base)
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


def _base_color(sub: np.ndarray, core: np.ndarray) -> np.ndarray:
    """The base map colour: the most common flat colour inside the matched shape (only used to
    measure contrast, it is never returned).
    """
    pix = _subsample(sub[core], 400_000).astype(np.int32)
    key = ((pix[:, 0] >> 4) << 8) | ((pix[:, 1] >> 4) << 4) | (pix[:, 2] >> 4)
    peak = int(np.bincount(key, minlength=4096).argmax())
    c = pix[key == peak].mean(0)
    near = np.abs(pix - c).sum(1) <= 30
    return pix[near].mean(0) if near.any() else c


def _stain_candidates(sub: np.ndarray, core: np.ndarray, base_rgb: np.ndarray) -> np.ndarray:
    """Pixels of `core` that stand out from the base colour, without lakes / rivers (bluish)."""
    pix = sub[core]
    base = _lab(base_rgb[None])[0]
    ok = np.empty(len(pix), bool)
    for s in range(0, len(pix), 400_000):
        lab = _lab(pix[s : s + 400_000])
        dab = np.hypot(lab[:, 1] - base[1], lab[:, 2] - base[2])
        de = np.hypot(lab[:, 0] - base[0], dab)
        a, b = lab[:, 1], lab[:, 2]
        water = (b < -3) & (a < -0.36 * b)  # light or dark blue, cyan: not a stain
        ok[s : s + 400_000] = (((dab >= AB_MIN) & (de >= DE_MIN)) | (de >= DE_STRONG)) & ~water
    cand = np.zeros(core.shape, bool)
    cand[core] = ok
    return cand


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
) -> dict:
    """arr: work-size RGB (used to find the map). full: the same image at full resolution (used to
    read the stains). outline: the country's lon/lat geometry. crop: fractions [x0, y0, x1, y1]
    that limit where the map is searched (None = whole image). borders: lon/lat lines of the
    first-level admin borders. cache: registrations are stored here (they are the slow part).

    Only the stains are returned (clipped to the outline). The base map is never part of the result.
    """
    h0, w0 = arr.shape[:2]
    fx0, fy0, fx1, fy1 = crop or (0.0, 0.0, 1.0, 1.0)
    x0 = int(np.clip(round(fx0 * w0), 0, max(0, w0 - MIN_CROP)))
    x1 = int(np.clip(round(fx1 * w0), min(w0, x0 + MIN_CROP), w0))
    y0 = int(np.clip(round(fy0 * h0), 0, max(0, h0 - MIN_CROP)))
    y1 = int(np.clip(round(fy1 * h0), min(h0, y0 + MIN_CROP), h0))
    sub = arr[y0:y1, x0:x1]

    # 1. where is the map (on the small image), and which pixel is which lon/lat
    key = (x0, y0, x1, y1, projection)
    reg = cache.get(key) if cache is not None else None
    if reg is None:
        reg = register(sub, outline, borders, projection)
        if cache is not None:
            cache[key] = reg
    a, b, tx, ty = reg["a"], reg["b"], reg["tx"], reg["ty"]
    inv = reg["inv"]

    # 2. move everything to the full-resolution crop
    big_h, big_w = full.shape[:2]
    kx, ky = big_w / w0, big_h / h0
    scale = max(1.0, (kx + ky) / 2)  # how much bigger the full image is than the small one
    fx_0 = min(big_w - 1, round(x0 * kx))
    fx_1 = max(fx_0 + 1, min(big_w, round(x1 * kx)))
    fy_0 = min(big_h - 1, round(y0 * ky))
    fy_1 = max(fy_0 + 1, min(big_h, round(y1 * ky)))
    img = full[fy_0:fy_1, fx_0:fx_1]
    hgt, wid = img.shape[:2]
    placed = affine_transform(reg["placed"], [kx, 0, 0, ky, x0 * kx - fx_0, y0 * ky - fy_0])

    def to_lonlat(xy):
        sx = (xy[:, 0] + fx_0) / kx - x0  # back to crop-local small-image px
        sy = (xy[:, 1] + fy_0) / ky - y0
        return inv(np.column_stack([(sx - tx) / a, (sy - ty) / b]))

    def rad(n: int) -> int:  # a radius that is n px on the small image, in full-res px
        return max(1, round(n * scale))

    r_edge, r_thin, r_gap = rad(2), rad(1), rad(2)

    inside = _raster(placed, (hgt, wid))
    if inside.sum() < 100:
        msg = "The map was placed outside the crop. Try a bigger crop."
        raise StainError(msg)
    core = _erode(inside, r_edge)  # away from the drawn coast line
    if core.sum() < 100:
        msg = "The map was placed outside the crop. Try a bigger crop."
        raise StainError(msg)
    band = inside & ~core
    n_core = int(core.sum())

    # 3. ONE label per pixel: nearest stain colour, decided pixel by pixel
    base_rgb = _base_color(img, core)
    cand = _stain_candidates(img, core, base_rgb)
    solid = _open(cand, r_thin)  # thin things (borders, labels) don't define a stain colour
    centers = _find_centers(_subsample(img[solid], CENTER_SAMPLE), tol)
    lab = _assign(img, cand, centers, base_rgb, tol)

    # Strongest contrast first. A paler shade that only hugs a stronger one (a thin rim of
    # anti-aliasing) is not a stain. Thin parts and tiny stains are removed from the label map.
    base_l = _lab(base_rgb[None])[0]
    contrast = [float(np.linalg.norm(_lab(c[None])[0] - base_l)) for c in centers]
    stronger = np.zeros_like(core)
    for k in sorted(range(len(centers)), key=lambda i: -contrast[i]):
        own = lab == k
        m = _open(own, r_thin)
        if stronger.any():
            far = m & ~_dilate(stronger, rad(3))
            if _open(far, r_thin).sum() < 0.35 * m.sum():
                lab[own] = -1
                continue
        lab[own & ~m] = -1
        if m.sum() >= max(15 * scale * scale, MIN_CLASS_SHARE * n_core):
            stronger |= m
        else:
            lab[own] = -1

    # Thin gaps (borders crossing a stain, a line between two stains) take the label of the
    # pixels around them; the stains also reach the coast line that was eroded away.
    gaps = _close(lab >= 0, r_gap) & (lab < 0) & core
    lab = _propagate(lab, gaps, r_gap)
    lab = _propagate(lab, band, r_edge + 1)

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
        tag = f"Stain {len(classes) + 1}"
        classes.append({"hex": hx, "tag": tag, "share": round(int(counts[i]) / total, 4)})
        features.append(
            {
                "type": "Feature",
                "properties": {"tag": tag, "color": hx, "hex": hx},
                "geometry": json.loads(shapely.to_geojson(ll)),
            }
        )

    bx0, by0, bx1, by1 = reg["placed"].bounds
    padx, pady = 0.03 * (bx1 - bx0), 0.03 * (by1 - by0)
    return {
        "size": [w0, h0],
        "bbox": [
            max(0.0, (x0 + bx0 - padx) / w0),
            max(0.0, (y0 + by0 - pady) / h0),
            min(1.0, (x0 + bx1 + padx) / w0),
            min(1.0, (y0 + by1 + pady) / h0),
        ],
        "fit": {
            "confidence": round(float(max(0.0, min(1.0, reg["lift"]))), 3),
            "outline": round(reg["hit_o"], 3),
            "borders": None if reg["hit_b"] is None else round(reg["hit_b"], 3),
            "projection": reg["name"],
            "aspect": round(abs(a / b), 3),
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
