"""map_detect.py — Is there a drawn map somewhere in this image, and where? (no country needed).

A map screenshot pasted over a photo has a very particular look: a big, flat, LIGHT fill (cream,
white, pale grey) that is cut by thin, contrasty lines (borders, coast) and that ends in a crisp
silhouette. Skies, clouds, walls and white panels are light too, but they either have no lines
inside or have only soft, low-contrast structure. So, on a small copy of the image:

  1. take the light pixels and find the most common flat light colours (peaks of a coarse histogram);
  2. per colour, join its pixels into regions (closing absorbs the thin lines drawn inside);
  3. keep the regions whose inside has thin, contrasty non-fill pixels (borders, paint) and whose
     outline is a crisp edge.

Runs in a few tens of ms on a screenshot. Needs scipy (if it is missing, detection is skipped).
"""

import time

import numpy as np
from PIL import Image

try:
    from scipy.ndimage import binary_fill_holes, label
except ImportError:  # pragma: no cover
    label = None

DETECT_MAX = 480  # longest side of the image the detection runs on, px
LIGHT_MIN = 200  # darkest channel of a "light" pixel
FILL_TOL = 12  # L1 colour distance (R+G+B) at which a pixel counts as the same flat fill
MAX_PEAKS = 5  # flat light colours examined
MIN_PEAK_SHARE = 0.01  # a colour must cover this share of the image to be examined
CLOSE_R = 2  # lines up to ~2*R px wide are absorbed into the region
MIN_AREA = 0.01  # smallest region, share of the image
MAX_AREA = 0.85  # largest region (a whole-image white background is not a map)
EDGE_T = 60.0  # gradient magnitude that counts as a crisp edge
MIN_CRISP = 0.25  # share of the region outline that is a crisp edge
MIN_NONFILL = 0.02  # share of the inside that is lines / paint (a plain panel has none)
MAX_NONFILL = 0.5
MIN_INNER = 35.0  # mean colour distance of those pixels to the fill (borders are contrasty)
PAD = 0.02  # the returned box is grown by this much of the image


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
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
    return ~_dilate(~m, r)


def _gradient(s: np.ndarray) -> np.ndarray:
    f = s.astype(np.float32)
    gx = np.zeros_like(f)
    gy = np.zeros_like(f)
    gx[:, 1:-1] = f[:, 2:] - f[:, :-2]
    gy[1:-1] = f[2:] - f[:-2]
    return np.sqrt((gx**2 + gy**2).sum(2))


def detect_map(arr: np.ndarray) -> dict:
    """arr: RGB uint8 image. Returns {found, boxes: [{box: [x0, y0, x1, y1], score}], ms}.

    Boxes are fractions of the image (0..1), best first. found is None when detection can't run.
    """
    t0 = time.perf_counter()
    if label is None:
        return {"found": None, "boxes": [], "ms": 0}
    k = max(1, -(-max(arr.shape[:2]) // DETECT_MAX))
    s = np.asarray(Image.fromarray(arr).reduce(k) if k > 1 else Image.fromarray(arr)).astype(
        np.int16
    )
    h, w = s.shape[:2]
    grad = _gradient(s)
    light = s.min(2) >= LIGHT_MIN
    q = ((s[..., 0] >> 4) << 8) | ((s[..., 1] >> 4) << 4) | (s[..., 2] >> 4)
    left = light.copy()
    found = []
    for _ in range(MAX_PEAKS):
        cnt = np.bincount(q[left], minlength=4096)
        peak = int(cnt.argmax())
        if cnt[peak] < MIN_PEAK_SHARE * h * w:
            break
        col = s[(q == peak) & left].mean(0)
        fill = (np.abs(s - col).sum(2) <= FILL_TOL) & light
        left &= ~fill
        lab, n = label(_erode(_dilate(fill, CLOSE_R), CLOSE_R))
        for i in range(1, n + 1):
            m = lab == i
            area = m.sum() / (h * w)
            if not MIN_AREA <= area <= MAX_AREA:
                continue
            m = binary_fill_holes(m)
            core = _erode(m, 2)
            inside = core & ~fill
            n_core = int(core.sum())
            if not n_core or not inside.any():
                continue
            nonfill = inside.sum() / n_core
            inner = float(np.abs(s[inside] - col).sum(1).mean())
            crisp = float((grad[m & ~_erode(m, 1)] > EDGE_T).mean())
            if not (
                MIN_NONFILL <= nonfill <= MAX_NONFILL and inner >= MIN_INNER and crisp >= MIN_CRISP
            ):
                continue
            ys, xs = np.nonzero(m)
            box = [
                max(0.0, xs.min() / w - PAD),
                max(0.0, ys.min() / h - PAD),
                min(1.0, (xs.max() + 1) / w + PAD),
                min(1.0, (ys.max() + 1) / h + PAD),
            ]
            score = min(1.0, crisp / 0.5) * min(1.0, inner / 60) * min(1.0, nonfill / 0.1)
            found.append(
                {"box": [round(float(v), 4) for v in box], "score": round(float(score), 3)}
            )
    found.sort(key=lambda b: -b["score"])
    return {"found": bool(found), "boxes": found, "ms": round((time.perf_counter() - t0) * 1000, 1)}
