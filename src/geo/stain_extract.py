"""stain_extract.py — Turn a screenshot of a country map with painted areas ("stains") into polygons.

The map is found by REGISTRATION, not by guessing from colours (colours fail on photos, white
margins, near-white land...). The country is given, so its outline and first-level borders are known:

  1. _edges        crisp edges of the image (a drawn map is full of thin, sharp lines; a photo mostly isn't).
  2. _candidates   coarse search: slide the known outline + borders (rendered as thin lines) over the edge
                   map at many scales, per projection, scoring how many line pixels land on an edge
                   *above what chance would give in that neighbourhood* (so busy photo areas don't win).
  3. _refine       the best few placements are refined with annealed ICP (outline and borders solved
                   together, free x / y scale) against the edge pixels. Best confidence wins.
  4. classes       now the map's exact shape is known: colours are clustered ONLY inside it. The
                   biggest flat colour is the base map, thin classes are drawn lines, and the other
                   classes enclosed by land are the stains.
  5. polygons      each class mask -> smoothed sub-pixel polygon -> lon/lat (inverse transform),
                   clipped to the outline.

All pixel work is crop-local (the crop only limits where the search happens).
"""

import io
import math
import operator

import numpy as np
import shapely
from PIL import Image, ImageDraw, ImageFilter, ImageOps
from shapely.affinity import affine_transform

WORK_MAX = 800  # longest side of the working image, px
TOL = 40  # L1 colour distance (sum over R,G,B) that still counts as "the same flat colour"
MIN_CROP = 32  # smallest crop side, px
MIN_CLASS_SHARE = 0.0015  # a colour class smaller than this share of the map is ignored
RING_SUPPORT_MIN = (
    0.4  # share of a stain's surroundings that must be land / lines / outside the map
)
EDGE_T = 40.0  # gradient magnitude (sum over channels) that counts as an edge
COARSE_MAX = 400  # longest side of the image used by the coarse search
SCALE_STEP = 1.035  # ratio between two scales of the coarse search
MIN_MAP_FRACTION = 0.1  # smallest map considered, relative to the largest that fits the image
TOP_K = 5  # coarse candidates that get refined
ICP_ITERS = 60
SMOOTH = 1.5  # default blur (px) applied to class masks before vectorising: rounder polygons
UPSCALE = 3  # the blurred mask is upscaled by this before thresholding (sub-pixel contours)


class StainError(Exception):
    """A problem the user can act on (shown as a 422)."""


# ── image helpers ──────────────────────────────────────────────────────────


def load_image(data: bytes) -> np.ndarray:
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
    s = WORK_MAX / max(img.size)
    if s < 1:
        size = (max(1, round(img.width * s)), max(1, round(img.height * s)))
        img = img.resize(size, Image.Resampling.BOX)
    return np.asarray(img)


def _rank(m: np.ndarray, r: int, grow: bool) -> np.ndarray:
    im = Image.fromarray((m * 255).astype(np.uint8))
    f = ImageFilter.MaxFilter(2 * r + 1) if grow else ImageFilter.MinFilter(2 * r + 1)
    return np.asarray(im.filter(f)) > 0


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    return _rank(m, r, True)


def _erode(m: np.ndarray, r: int) -> np.ndarray:
    return _rank(m, r, False)


def _close(m: np.ndarray, r: int) -> np.ndarray:
    return _erode(_dilate(m, r), r)


def _mask_geom(m: np.ndarray, simplify: float = 0.8):
    """Boolean mask -> polygon(s) in pixel coordinates (x right, y down)."""
    p = np.pad(m, ((0, 0), (1, 1))).astype(np.int8)
    d = np.diff(p, axis=1)
    ys, xs0 = np.nonzero(d == 1)
    _, xs1 = np.nonzero(d == -1)
    if not len(ys):
        return shapely.GeometryCollection()
    g = shapely.union_all(shapely.box(xs0, ys, xs1, ys + 1))
    return shapely.simplify(g, simplify) if simplify else g


def _smooth_geom(m: np.ndarray, smooth: float):
    """Like _mask_geom but with round, sub-pixel contours: blur, upscale, threshold, vectorise."""
    if smooth <= 0:
        return _mask_geom(m, 0.8)
    im = Image.fromarray((m * 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(smooth))
    im = im.resize((m.shape[1] * UPSCALE, m.shape[0] * UPSCALE), Image.Resampling.BICUBIC)
    g = _mask_geom(np.asarray(im) >= 128, 1.0)
    return affine_transform(g, [1 / UPSCALE, 0, 0, 1 / UPSCALE, 0, 0])


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


# ── projections ────────────────────────────────────────────────────────────


def _fwd(name: str, lat0: float):
    if name == "equirect":
        k = math.cos(math.radians(lat0))
        return lambda xy: np.column_stack([xy[:, 0] * k, xy[:, 1]])
    return lambda xy: np.column_stack(
        [
            np.radians(xy[:, 0]),
            np.log(np.tan(np.pi / 4 + np.radians(np.clip(xy[:, 1], -85, 85)) / 2)),
        ]
    )


def _inv(name: str, lat0: float):
    if name == "equirect":
        k = math.cos(math.radians(lat0)) or 1e-6
        return lambda uv: np.column_stack([uv[:, 0] / k, uv[:, 1]])
    return lambda uv: np.column_stack(
        [np.degrees(uv[:, 0]), np.degrees(2 * np.arctan(np.exp(uv[:, 1])) - np.pi / 2)]
    )


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


def _candidates(e_coarse: np.ndarray, f: int, uv, uvb) -> list[dict]:
    """Best placement of the known lines at every scale. Returns [{lift, a, tx, ty}] in FULL-res
    px for x = a*u + tx, y = -a*v + ty.
    """
    hh, ww = e_coarse.shape
    ub = uv.bounds
    tw, th = ub[2] - ub[0], ub[3] - ub[1]
    if tw <= 0 or th <= 0:
        return []
    diag = math.hypot(tw, th)
    samples = [shapely.get_coordinates(shapely.segmentize(uv, diag / 1500))]
    if uvb is not None:
        samples.append(shapely.get_coordinates(shapely.segmentize(uvb, diag / 1500)))
    pts = np.concatenate(samples)

    b = _dilate(e_coarse, 1).astype(np.float32)
    sh = (_fast_len(hh), _fast_len(ww))
    fb = np.fft.rfft2(b, sh)
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
            corr = np.fft.irfft2(fb * np.conj(np.fft.rfft2(t, sh)), sh)[: hh - h + 1, : ww - w + 1]
            hit = corr / n_t
            box = (
                integ[h:, w:][: hh - h + 1, : ww - w + 1]
                - integ[:-h, w:][: hh - h + 1, : ww - w + 1]
                - integ[h:, :-w][: hh - h + 1, : ww - w + 1]
                + integ[:-h, :-w][: hh - h + 1, : ww - w + 1]
            )
            dens = box / (h * w)
            lift = (hit - dens) / (1 - dens + 0.05)
            iy, ix = np.unravel_index(int(np.argmax(lift)), lift.shape)
            out.append(
                {
                    "lift": float(lift[iy, ix]),
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
    po = _subsample(shapely.get_coordinates(shapely.segmentize(uv, diag / 150)), 1500)
    pb = None
    if uvb is not None:
        pb = _subsample(shapely.get_coordinates(shapely.segmentize(uvb, diag / 200)), 1500)

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
    exy = _subsample(np.column_stack([ex + xs0 + 0.5, ey + ys0 + 0.5]), 30000)
    tree = shapely.STRtree(shapely.points(exy))
    size = max(x1 - x0, y1 - y0)

    for i in range(ICP_ITERS):
        maxd = size * max(0.01, 0.06 * 0.92**i)
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
    """Find where the country is in `sub` and the pixel <-> lon/lat transform. Crop-local."""
    hgt, wid = sub.shape[:2]
    fit_outline, clip_outline = _prep_outline(outline)
    lat0 = fit_outline.centroid.y
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

    e = _edges(sub)
    e_dil = _dilate(e, 2)
    f = max(1, math.ceil(max(hgt, wid) / COARSE_MAX))
    hc, wc = hgt // f, wid // f
    e_coarse = e[: hc * f, : wc * f].reshape(hc, f, wc, f).max((1, 3))

    names = ["equirect", "mercator"] if projection == "auto" else [projection]
    cands = []
    for nm in names:
        fwd = _fwd(nm, lat0)
        uv = shapely.transform(fit_outline, fwd)
        uvb = shapely.transform(inner, fwd) if inner is not None else None
        cands.extend(
            {**c, "name": nm, "uv": uv, "uvb": uvb} for c in _candidates(e_coarse, f, uv, uvb)
        )
    if not cands:
        msg = "The map doesn't fit in this crop. Use a bigger crop."
        raise StainError(msg)

    # best few placements, skipping near-duplicates (same projection, similar scale and spot)
    cands.sort(key=lambda c: -c["lift"])
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
            best = {**r, "name": c["name"], "uv": c["uv"], "uvb": c["uvb"]}
    m = [best["a"], 0, 0, best["b"], best["tx"], best["ty"]]
    return {
        **{k: best[k] for k in ("a", "b", "tx", "ty", "hit_o", "hit_b", "lift", "name")},
        "lat0": lat0,
        "placed": affine_transform(best["uv"], m),
        "placed_b": affine_transform(best["uvb"], m) if best["uvb"] is not None else None,
        "clip_outline": clip_outline,
        "fit_outline": fit_outline,
    }


# ── colour classes (inside the matched shape only) ─────────────────────────


def _cluster(a: np.ndarray, mask: np.ndarray, tol: int = TOL, max_k: int = 14):
    """Peak-based colour clustering of the pixels in `mask`. Labels HxW (-1 = none), centers Kx3."""
    h, w, _ = a.shape
    flat = np.flatnonzero(mask.ravel())
    pix = a.reshape(-1, 3)[flat].astype(np.int16)
    labels = np.full(h * w, -1, np.int16)
    centers: list[np.ndarray] = []
    idx = np.arange(len(pix))
    min_px = max(30, int(MIN_CLASS_SHARE * len(pix) * 0.5))
    for k in range(max_k):
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
        labels[flat[idx[take]]] = k
        centers.append(c)
        idx = idx[~take]
    return labels.reshape(h, w), np.array(centers)


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
    outline,
    crop: list[float] | None = None,
    projection: str = "auto",
    clip: bool = True,
    tol: int = TOL,
    borders=None,
    smooth: float = SMOOTH,
    cache: dict | None = None,
) -> dict:
    """arr: work-size RGB. outline: the country's lon/lat geometry. crop: fractions [x0, y0, x1, y1]
    that limit where the map is searched (None = whole image). borders: lon/lat lines of the
    first-level admin borders. cache: registrations are stored here (they are the slow part).
    """
    import json  # noqa: PLC0415

    h0, w0 = arr.shape[:2]
    fx0, fy0, fx1, fy1 = crop or (0.0, 0.0, 1.0, 1.0)
    x0 = int(np.clip(round(fx0 * w0), 0, max(0, w0 - MIN_CROP)))
    x1 = int(np.clip(round(fx1 * w0), min(w0, x0 + MIN_CROP), w0))
    y0 = int(np.clip(round(fy0 * h0), 0, max(0, h0 - MIN_CROP)))
    y1 = int(np.clip(round(fy1 * h0), min(h0, y0 + MIN_CROP), h0))
    sub = arr[y0:y1, x0:x1]
    hgt, wid = sub.shape[:2]

    # 1. where is the map, and which pixel is which lon/lat
    key = (x0, y0, x1, y1, projection)
    reg = cache.get(key) if cache is not None else None
    if reg is None:
        reg = register(sub, outline, borders, projection)
        if cache is not None:
            cache[key] = reg
    a, b, tx, ty = reg["a"], reg["b"], reg["tx"], reg["ty"]
    inv = _inv(reg["name"], reg["lat0"])

    def to_lonlat(xy):
        return inv(np.column_stack([(xy[:, 0] - tx) / a, (xy[:, 1] - ty) / b]))

    inside = _raster(reg["placed"], (hgt, wid))
    if inside.sum() < 100:
        msg = "The map was placed outside the crop. Try a bigger crop."
        raise StainError(msg)
    core = _erode(inside, 2)  # away from the drawn coast line
    band = inside & ~core

    # 2. colour classes inside the map only
    lab, centers = _cluster(sub, core, tol)
    if not len(centers):
        msg = "No flat colours found inside the matched area."
        raise StainError(msg)
    n_core = int(core.sum())
    stats = []
    for k in range(len(centers)):
        m = lab == k
        n = int(m.sum())
        if n >= MIN_CLASS_SHARE * n_core:
            stats.append({"k": k, "n": n, "thin": bool(_erode(m, 1).sum() / n < 0.45)})
    solid = [s for s in stats if not s["thin"]]
    if not solid:
        msg = "No filled areas found inside the matched area."
        raise StainError(msg)
    land = max(solid, key=operator.itemgetter("n"))
    thin = [s["k"] for s in stats if s["thin"]]
    allowed = [land["k"], *thin]
    stains = []
    for s in solid:
        if s is land:
            continue
        m = lab == s["k"]
        ring = _dilate(m, 4) & ~_dilate(m, 2)
        if ring.any():
            sup = float((np.isin(lab[ring], allowed) | ~inside[ring]).mean())
            if sup >= RING_SUPPORT_MIN:
                stains.append(s)
    stains.sort(key=lambda s: -s["n"])

    # 3. polygons per class, in lon/lat
    classes, features = [], []
    total = sum(s["n"] for s in [land, *stains]) or 1
    for i, (s, is_land) in enumerate([(s, False) for s in stains] + [(land, True)]):
        m = _close(lab == s["k"], 2)
        m |= _dilate(m, 3) & band  # reach the coast line that was eroded away
        g = _smooth_geom(m, smooth)
        parts = [p for p in _polys(g) if p.area >= 8]
        if not parts:
            continue
        ll = shapely.make_valid(shapely.transform(shapely.union_all(parts), to_lonlat))
        if clip:
            ll = shapely.intersection(ll, reg["clip_outline"])
        ll = _poly_only(ll)
        if ll.is_empty:
            continue
        try:
            ll = shapely.set_precision(ll, 1e-5)
        except Exception:  # noqa: BLE001
            pass
        hx = _hex(centers[s["k"]])
        tag = "Base map" if is_land else f"Stain {i + 1}"
        classes.append({"hex": hx, "tag": tag, "land": is_land, "share": round(s["n"] / total, 4)})
        features.append(
            {
                "type": "Feature",
                "properties": {"tag": tag, "color": hx, "hex": hx, "land": is_land},
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
        "geojson": {"type": "FeatureCollection", "features": features},
        "overlay": {
            "outline": _path(reg["placed"], x0, y0),
            "borders": _line_path(reg["placed_b"], x0, y0),
        },
    }
