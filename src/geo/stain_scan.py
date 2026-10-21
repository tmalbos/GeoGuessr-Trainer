"""Grid scan for the manual stain warp.

The automatic fit and the manual warp use the same four degrees of freedom:
independent X/Y scale plus translation. This module scores a dense grid around one
configuration and writes a CSV plus a short Markdown summary. It is diagnostic only:
nothing here feeds the registration or the stain polygons.
"""

from __future__ import annotations

import csv
import io
import math
import operator
import zipfile
from pathlib import Path

import numpy as np
import shapely

from src.geo import stain_extract as se

COMBINED = 0.5  # outline / borders weight, same as the match key


def _crop(arr: np.ndarray, crop: list[float] | None):
    h0, w0 = arr.shape[:2]
    fx0, fy0, fx1, fy1 = crop or (0.0, 0.0, 1.0, 1.0)
    x0 = int(np.clip(round(fx0 * w0), 0, max(0, w0 - se.MIN_CROP)))
    x1 = int(np.clip(round(fx1 * w0), min(w0, x0 + se.MIN_CROP), w0))
    y0 = int(np.clip(round(fy0 * h0), 0, max(0, h0 - se.MIN_CROP)))
    y1 = int(np.clip(round(fy1 * h0), min(h0, y0 + se.MIN_CROP), h0))
    return x0, y0, x1, y1, arr[y0:y1, x0:x1]


def _sample(geom, dt: np.ndarray | None) -> np.ndarray | None:
    if dt is None or geom is None or geom.is_empty:
        return None
    pts = shapely.get_coordinates(shapely.segmentize(geom, se.MATCH_STEP_PX))
    return pts if len(pts) >= 10 else None


def _metrics(
    pts: np.ndarray,
    sx: float,
    sy: float,
    dx: float,
    dy: float,
    px: float,
    py: float,
    dt: np.ndarray,
):
    hh, ww = dt.shape
    x = sx * (pts[:, 0] - px) + px + dx
    y = sy * (pts[:, 1] - py) + py + dy
    xs = np.rint(x - 0.5).astype(int)
    ys = np.rint(y - 0.5).astype(int)
    ok = (xs >= 0) & (xs < ww) & (ys >= 0) & (ys < hh)
    if ok.sum() < 10:
        return None
    d = dt[ys[ok], xs[ok]]
    return {
        "precision": float(np.mean(d <= se.MATCH_TOL_PX)),
        "median": float(np.median(d)),
        "p90": float(np.quantile(d, 0.9)),
        "n": int(ok.sum()),
        "x": x[ok],
        "y": y[ok],
        "d": d,
    }


def _regions(m: dict) -> dict:
    x, y, d = m["x"], m["y"], m["d"]
    cx, cy = (float(x.min()) + float(x.max())) / 2, (float(y.min()) + float(y.max())) / 2
    out = {}
    for name, mask in (
        ("nw", (x < cx) & (y < cy)),
        ("ne", (x >= cx) & (y < cy)),
        ("sw", (x < cx) & (y >= cy)),
        ("se", (x >= cx) & (y >= cy)),
    ):
        if int(mask.sum()) < 5:
            out[name] = None
        else:
            out[name] = {
                "precision": float(np.mean(d[mask] <= se.MATCH_TOL_PX)),
                "median": float(np.median(d[mask])),
                "n": int(mask.sum()),
            }
    return out


def _axis(center: float, span: float, step: float) -> np.ndarray:
    if step <= 0 or span <= 0:
        return np.array([center], dtype=float)
    n = math.floor(span / step + 1e-9)
    vals = center + np.arange(-n, n + 1) * step
    return np.round(vals, 4)


def scan_warps(
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
    *,
    span_pct: float = 2.0,
    span_px: float = 2.0,
    step_pct: float = 0.1,
    step_px: float = 0.1,
) -> tuple[list[dict], dict]:
    """Score every scale/translation on a grid around the given configuration.

    The grid is centred on the supplied values, so the exact manual point is always in it.
    `span_pct` is in percentage points (2.0 = +/-2%), `span_px` in work-image px.
    """
    x0, y0, x1, y1, sub = _crop(arr, crop)
    key = (x0, y0, x1, y1, projection)
    reg = cache.get(key) if cache is not None else None
    if reg is None:
        reg = se.register(sub, outline, borders, projection)
        if cache is not None:
            cache[key] = reg
    dt = reg.get("dt")
    if dt is None:
        msg = "No distance map (scipy is required for the scan)."
        raise se.StainError(msg)

    px, py = float(pivot[0]) - x0, float(pivot[1]) - y0
    op = _sample(reg["placed"], dt)
    bp = _sample(reg["placed_b"], dt) if reg["placed_b"] is not None else None
    if op is None:
        msg = "The placed outline has too few points to scan."
        raise se.StainError(msg)

    scales_x = _axis(100.0 * sx, span_pct, step_pct)
    scales_y = _axis(100.0 * sy, span_pct, step_pct)
    shifts_x = _axis(dx, span_px, step_px)
    shifts_y = _axis(dy, span_px, step_px)

    rows: list[dict] = []
    for sx_pct in scales_x:
        nsx = float(sx_pct) / 100.0
        for sy_pct in scales_y:
            nsy = float(sy_pct) / 100.0
            for ndx in shifts_x:
                for ndy in shifts_y:
                    mo = _metrics(op, nsx, nsy, float(ndx), float(ndy), px, py, dt)
                    if mo is None:
                        continue
                    mb = (
                        _metrics(bp, nsx, nsy, float(ndx), float(ndy), px, py, dt)
                        if bp is not None
                        else None
                    )
                    rg = _regions(mo)
                    out = round(mo["precision"] * 100, 2)
                    bor = None if mb is None else round(mb["precision"] * 100, 2)
                    comb = out if bor is None else round(COMBINED * (out + bor), 2)
                    region_pairs = [
                        (name, None if rg[name] is None else round(rg[name]["precision"] * 100, 2))
                        for name in ("nw", "ne", "sw", "se")
                    ]
                    usable = [(name, val) for name, val in region_pairs if val is not None]
                    worst_name, worst_pct = (
                        min(usable, key=operator.itemgetter(1)) if usable else (None, None)
                    )
                    row = {
                        "scale_x_pct": round(float(sx_pct), 2),
                        "scale_y_pct": round(float(sy_pct), 2),
                        "shift_x_px": round(float(ndx), 2),
                        "shift_y_px": round(float(ndy), 2),
                        "outline_pct": out,
                        "outline_median_px": round(mo["median"], 2),
                        "outline_p90_px": round(mo["p90"], 2),
                        "borders_pct": bor,
                        "borders_median_px": None if mb is None else round(mb["median"], 2),
                        "borders_p90_px": None if mb is None else round(mb["p90"], 2),
                        "combined_pct": comb,
                        "nw_pct": None
                        if rg["nw"] is None
                        else round(rg["nw"]["precision"] * 100, 2),
                        "ne_pct": None
                        if rg["ne"] is None
                        else round(rg["ne"]["precision"] * 100, 2),
                        "sw_pct": None
                        if rg["sw"] is None
                        else round(rg["sw"]["precision"] * 100, 2),
                        "se_pct": None
                        if rg["se"] is None
                        else round(rg["se"]["precision"] * 100, 2),
                        "worst_region": worst_name,
                        "worst_pct": worst_pct,
                        "nw_median_px": None if rg["nw"] is None else round(rg["nw"]["median"], 2),
                        "ne_median_px": None if rg["ne"] is None else round(rg["ne"]["median"], 2),
                        "sw_median_px": None if rg["sw"] is None else round(rg["sw"]["median"], 2),
                        "se_median_px": None if rg["se"] is None else round(rg["se"]["median"], 2),
                        "aspect": round(abs(reg["a"] * nsx / (reg["b"] * nsy)), 3),
                    }
                    rows.append(row)

    rows.sort(key=lambda r: (-r["combined_pct"], -r["outline_pct"], r["outline_median_px"]))
    for i, row in enumerate(rows, 1):
        row["rank_combined"] = i

    meta = {
        "center": {
            "scale_x_pct": round(100.0 * sx, 2),
            "scale_y_pct": round(100.0 * sy, 2),
            "shift_x_px": round(float(dx), 2),
            "shift_y_px": round(float(dy), 2),
        },
        "span_pct": span_pct,
        "span_px": span_px,
        "step_pct": step_pct,
        "step_px": step_px,
        "rows": len(rows),
        "crop": [x0, y0, x1, y1],
    }
    return rows, meta


def _row_text(r: dict) -> str:
    return (
        f"X {r['scale_x_pct']:.2f}% · Y {r['scale_y_pct']:.2f}% · "
        f"dx {r['shift_x_px']:+.2f}px · dy {r['shift_y_px']:+.2f}px"
    )


def _table(rows: list[dict]) -> str:
    head = (
        "| # | Scale X | Scale Y | Shift X | Shift Y | Outline | Border | Combined | NW | NE | SW | SE |\n"
        "|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    )

    def pv(v) -> str:
        return "—" if v is None else f"{v:.2f}%"

    body = [
        f"| {r['rank_combined']} | {r['scale_x_pct']:.2f}% | {r['scale_y_pct']:.2f}% | "
        f"{r['shift_x_px']:+.2f} | {r['shift_y_px']:+.2f} | {r['outline_pct']:.2f}% | "
        f"{pv(r['borders_pct'])} | {r['combined_pct']:.2f}% | {pv(r['nw_pct'])} | {pv(r['ne_pct'])} | "
        f"{pv(r['sw_pct'])} | {pv(r['se_pct'])} |"
        for r in rows
    ]
    return head + "\n".join(body) + "\n"


def summary_markdown(rows: list[dict], meta: dict) -> str:
    if not rows:
        return "# Stain warp scan\n\nNo rows were produced.\n"
    best = rows[0]
    best_out = max(rows, key=operator.itemgetter("outline_pct"))
    center = meta["center"]
    c = min(
        rows,
        key=lambda r: (
            abs(r["scale_x_pct"] - center["scale_x_pct"])
            + abs(r["scale_y_pct"] - center["scale_y_pct"])
            + abs(r["shift_x_px"] - center["shift_x_px"])
            + abs(r["shift_y_px"] - center["shift_y_px"])
        ),
    )
    near = sum(1 for r in rows if best_out["outline_pct"] - r["outline_pct"] <= 1.0) / len(rows)

    def pv(v) -> str:
        return "—" if v is None else f"{v:.2f}%"

    weak = min(
        (
            ("NW", best["nw_pct"]),
            ("NE", best["ne_pct"]),
            ("SW", best["sw_pct"]),
            ("SE", best["se_pct"]),
        ),
        key=lambda p: -1.0 if p[1] is None else p[1],
    )
    best_worst = max(rows, key=lambda r: -1.0 if r["worst_pct"] is None else r["worst_pct"])
    weak_key = weak[0].lower() + "_pct"
    weak_ceiling = max((r[weak_key] for r in rows if r[weak_key] is not None), default=None)
    lines = [
        "# Stain warp scan",
        "",
        (
            f"- Grid: **{meta['rows']:,}** configurations, centred on **X {center['scale_x_pct']:.2f}% · "
            f"Y {center['scale_y_pct']:.2f}% · dx {center['shift_x_px']:+.2f}px · dy {center['shift_y_px']:+.2f}px**."
        ),
        f"- Range: scale X/Y **±{meta['span_pct']:g}%**, shifts **±{meta['span_px']:g} px**, step **{meta['step_pct']:g}% / {meta['step_px']:g} px**.",
        f"- Best combined: **{_row_text(best)}** → outline **{best['outline_pct']:.2f}%**, borders **{pv(best['borders_pct'])}**, combined **{best['combined_pct']:.2f}%**.",
        f"- Best outline: **{_row_text(best_out)}** → outline **{best_out['outline_pct']:.2f}%**, borders **{pv(best_out['borders_pct'])}**.",
        f"- Your centre ranks **#{c['rank_combined']} / {len(rows):,}** by combined; outline **{c['outline_pct']:.2f}%**, borders **{pv(c['borders_pct'])}**.",
        f"- **{near:.1%}** of the grid is within 1 point of the best outline. "
        + (
            "That is a broad plateau: the metric does not distinguish the exact spot."
            if near >= 0.1
            else "That is a sharp peak: the score is picky about the placement."
        ),
        f"- Weakest region at the best combined point: **{weak[0]} {pv(weak[1])}**. The region split is in the CSV for every row.",
        (
            f"- Best worst-region point: **{_row_text(best_worst)}** → worst region **{best_worst['worst_region'].upper()} {pv(best_worst['worst_pct'])}**, "
            f"outline **{best_worst['outline_pct']:.2f}%**, borders **{pv(best_worst['borders_pct'])}**."
        ),
        f"- Across the whole grid, **{weak[0]}** peaks at **{pv(weak_ceiling)}**. "
        + (
            f"No global 4-DOF warp gets {weak[0]} above that: the residual error is in the shape/registration, not the final nudge."
            if weak_ceiling is not None and weak_ceiling < 80
            else f"The global warp can still trade other regions for {weak[0]}; use the worst-region row above if {weak[0]} is the visual bottleneck."
        ),
        "",
        "## Top 20 by combined",
        "",
        _table(rows[:20]),
        "## Your centre",
        "",
        _table([{**c, "rank_combined": c["rank_combined"]}]),
    ]
    return "\n".join(lines) + "\n"


def report_zip(rows: list[dict], meta: dict) -> tuple[bytes, str]:
    summary = summary_markdown(rows, meta)
    fields = list(rows[0].keys()) if rows else []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        csv_buf = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buf, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
        z.writestr("stain_scan.csv", csv_buf.getvalue())
        z.writestr("stain_scan_summary.md", summary)
    return buf.getvalue(), summary


def write_last_report(data: bytes, name: str = "stain_scan_last.zip") -> Path:
    out = Path("D:/AppData/Local/Temp/opencode") / name
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(data)
    return out
