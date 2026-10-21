"""stain.py — Technical tool: screenshot of a painted country map -> polygons.

Flow:  POST /technical/stain/upload   -> raw image bytes; kept in memory, returns a token and where a map
                                         was detected in the image (generic detection, no country needed)
       POST /technical/stain/analyze  -> {token, country, crop, ...} -> georeferenced stain polygons per colour.
                                         Nothing runs when no map was detected (unless the user drew a crop box);
                                         otherwise the detected box biases the search for the map.
Uploads live in memory only (last few); a server restart means loading the image again (410).
Only the stains are returned: the base map is the app's own country map and is never extracted.
The maths is in src/geo/stain_extract.py; the "is there a map?" check is in src/geo/map_detect.py.
"""

import contextlib
import os
import re
import subprocess
import sys
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Literal
from uuid import uuid4

import shapely
from fastapi import APIRouter, HTTPException, Request, Response
from PIL import Image
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from src.api.deps import get_ctx
from src.geo.map_detect import detect_map
from src.geo.stain_extract import TOL, StainError, analyze, evaluate_warp, load_image
from src.geo.stain_scan import write_last_report

router = APIRouter()

MAX_UPLOADS = 5
_UPLOADS: OrderedDict[str, dict] = OrderedDict()
_OUTLINES: dict[str, object] = {}
_BORDERS: dict[str, object] = {}
_LOCK = threading.Lock()


def _outline(local, code: str):
    """The country's lon/lat geometry from the local geo layers (cached), or None."""
    if code in _OUTLINES:
        return _OUTLINES[code]
    geom = None
    c = local.countries
    if c is not None and not c.empty:
        sel = c[c["code"] == code]
        if len(sel):
            geom = shapely.union_all(shapely.make_valid(sel.geometry.to_numpy()))
    if geom is None:
        layer = local._admin_layer(code)  # noqa: SLF001
        if layer is not None and len(layer):
            geom = shapely.union_all(shapely.make_valid(layer.geometry.to_numpy()))
    if geom is not None:
        _OUTLINES[code] = geom
    return geom


def _borders(local, code: str):
    """First-level admin borders of the country as lon/lat lines (cached), or None.
    Built by dissolving the finest admin layer by `state`; shared borders appear once.
    """
    if code in _BORDERS:
        return _BORDERS[code]
    out = None
    layer = local._admin_layer(code)  # noqa: SLF001
    if layer is not None and len(layer) and "state" in layer.columns:
        lines = []
        for state, grp in layer.groupby("state"):
            if not state:
                continue
            g = shapely.union_all(shapely.make_valid(grp.geometry.to_numpy()))
            lines.append(shapely.simplify(g, 0.01).boundary)
        if lines:
            out = shapely.union_all(lines)
    _BORDERS[code] = out
    return out


def _prepare(data: bytes) -> dict:
    # "full" = the image the stains are read from; "arr" = small copy used to find the map.
    # "reg" caches the (slow) map registration per country / crop / projection
    full, work = load_image(data)
    return {"full": full, "arr": work, "reg": {}, "map": detect_map(work)}


@router.post("/technical/stain/upload")
async def stain_upload(request: Request):
    """Raw image bytes in the body."""
    raw = await request.body()
    if not raw:
        raise HTTPException(422, "Empty file")
    try:
        up = await run_in_threadpool(_prepare, raw)
    except StainError as e:
        raise HTTPException(422, str(e)) from e
    token = uuid4().hex
    with _LOCK:
        _UPLOADS[token] = up
        while len(_UPLOADS) > MAX_UPLOADS:
            _UPLOADS.popitem(last=False)
    h, w = up["full"].shape[:2]
    return {"token": token, "width": w, "height": h, "map": up["map"]}


class StainBody(BaseModel):
    token: str
    country: str
    crop: list[float] | None = Field(None, min_length=4, max_length=4)
    projection: Literal["auto", "equirect", "plate", "mercator", "lcc", "albers"] = "auto"
    tol: int = Field(TOL, ge=8, le=120)  # shades closer than this are merged into one stain


@router.post("/technical/stain/analyze")
async def stain_analyze(body: StainBody, request: Request):
    up = _UPLOADS.get(body.token)
    if up is None:
        raise HTTPException(410, "That upload expired. Add the image again.")
    det = up["map"]
    if det["found"] is False and body.crop is None:
        # No map in the image: matching a country against a photo would only produce a random placement.
        # A box drawn by the user is an explicit "it is here", so that case still runs.
        raise HTTPException(
            422, "No map was found in this image. Draw a box around it to search anyway."
        )
    hint = (
        det["boxes"][0]["box"] if det["found"] else None
    )  # best box: a rough guess, only biases the search
    if not re.fullmatch(r"[A-Za-z]{2}", body.country):
        raise HTTPException(422, "Invalid country code")
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    code = body.country.upper()
    outline = await run_in_threadpool(_outline, local, code)
    if outline is None:
        raise HTTPException(422, f"No local outline for {code} (see scripts/convert_geo.py)")
    borders = await run_in_threadpool(_borders, local, code)
    try:
        return await run_in_threadpool(
            analyze,
            up["arr"],
            up["full"],
            outline,
            body.crop,
            body.projection,
            body.tol,
            borders,
            up["reg"].setdefault(code, {}),
            hint,
        )
    except StainError as e:
        raise HTTPException(422, str(e)) from e


class WarpBody(BaseModel):
    token: str
    country: str
    crop: list[float] | None = Field(None, min_length=4, max_length=4)
    projection: Literal["auto", "equirect", "plate", "mercator", "lcc", "albers"] = "auto"
    sx: float = Field(1.0, gt=0.05, le=20.0)  # independent X / Y scale, same as the fit
    sy: float = Field(1.0, gt=0.05, le=20.0)
    dx: float = Field(0.0, ge=-10_000.0, le=10_000.0)  # work-image px
    dy: float = Field(0.0, ge=-10_000.0, le=10_000.0)
    pivot: list[float] = Field(..., min_length=2, max_length=2)


@router.post("/technical/stain/warp")
async def stain_warp(body: WarpBody, request: Request):
    """Rescore a manual warp (no stains): the same scale/translation family the fit uses."""
    up = _UPLOADS.get(body.token)
    if up is None:
        raise HTTPException(410, "That upload expired. Add the image again.")
    if not re.fullmatch(r"[A-Za-z]{2}", body.country):
        raise HTTPException(422, "Invalid country code")
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    code = body.country.upper()
    outline = await run_in_threadpool(_outline, local, code)
    if outline is None:
        raise HTTPException(422, f"No local outline for {code} (see scripts/convert_geo.py)")
    borders = await run_in_threadpool(_borders, local, code)
    try:
        return await run_in_threadpool(
            evaluate_warp,
            up["arr"],
            outline,
            borders,
            body.crop,
            body.projection,
            up["reg"].setdefault(code, {}),
            body.sx,
            body.sy,
            body.dx,
            body.dy,
            body.pivot,
        )
    except StainError as e:
        raise HTTPException(422, str(e)) from e


class ScanBody(WarpBody):
    span_pct: float = Field(2.0, gt=0.0, le=10.0)  # +/- percentage points
    span_px: float = Field(2.0, gt=0.0, le=100.0)  # +/- work px
    step_pct: float = Field(0.1, gt=0.0, le=5.0)
    step_px: float = Field(0.1, gt=0.0, le=5.0)


def _run_scan(cmd: list[str], root: Path):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    return subprocess.run(
        cmd,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        timeout=1800,
    )


@router.post("/technical/stain/scan")
async def stain_scan(body: ScanBody, request: Request):
    """Scan a +/- range around a warp and return a ZIP with summary + top rows + Parquet.

    The grid is run in a separate process so the API worker stays responsive and
    the scan can use all cores. The full 0.1% / 0.1 px ±2% grid takes about 13 minutes
    on the 6-core reference machine.
    """
    up = _UPLOADS.get(body.token)
    if up is None:
        raise HTTPException(410, "That upload expired. Add the image again.")
    if not re.fullmatch(r"[A-Za-z]{2}", body.country):
        raise HTTPException(422, "Invalid country code")
    nx = int(body.span_pct / body.step_pct) * 2 + 1
    nsx = int(body.span_px / body.step_px) * 2 + 1
    if nx * nx * nsx * nsx > 3_000_000:
        raise HTTPException(422, "That grid is too large. Use a bigger step.")
    local = get_ctx(request).local_geo
    if local is None:
        raise HTTPException(503, "Geo layers are not loaded yet. Check the Dashboard.")
    code = body.country.upper()
    if await run_in_threadpool(_outline, local, code) is None:
        raise HTTPException(422, f"No local outline for {code} (see scripts/convert_geo.py)")

    root = Path(__file__).resolve().parents[3]
    tmp = Path("D:/AppData/Local/Temp/opencode")
    tmp.mkdir(parents=True, exist_ok=True)
    stem = f"stain_scan_{uuid4().hex}"
    image_path = tmp / f"{stem}.png"
    out_base = tmp / stem
    try:
        Image.fromarray(up["full"]).save(image_path)
        cmd = [
            sys.executable,
            str(root / "scripts" / "stain_scan_report.py"),
            "--image",
            str(image_path),
            "--country",
            code,
            "--projection",
            body.projection,
            "--center",
            f"{body.sx * 100:.4f}",
            f"{body.sy * 100:.4f}",
            f"{body.dx:.4f}",
            f"{body.dy:.4f}",
            "--span-pct",
            f"{body.span_pct:g}",
            "--span-px",
            f"{body.span_px:g}",
            "--step-pct",
            f"{body.step_pct:g}",
            "--step-px",
            f"{body.step_px:g}",
            "--workers",
            str(max(1, min(8, os.cpu_count() or 4))),
            "--out",
            str(out_base),
            "--zip",
            "--no-csv",
        ]
        if body.crop:
            cmd += ["--crop", *(f"{v:g}" for v in body.crop)]
        try:
            proc = await run_in_threadpool(_run_scan, cmd, root)
        except subprocess.TimeoutExpired as e:
            raise HTTPException(504, "The scan took longer than 30 minutes.") from e
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-12:]
            raise HTTPException(500, "Scan failed:\n" + "\n".join(tail))
        zip_path = out_base.with_suffix(".zip")
        if not zip_path.exists():
            raise HTTPException(500, "Scan finished without a report.")
        data = zip_path.read_bytes()
        write_last_report(data)
        filename = f"{code}-stain-scan.zip"
        return Response(
            content=data,
            media_type="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    finally:
        for p in tmp.glob(stem + "*"):
            with contextlib.suppress(OSError):
                p.unlink()
