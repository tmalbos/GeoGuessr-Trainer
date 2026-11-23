"""mapshaper_tool.py — Find and run mapshaper (Node command line), used to write TopoJSON exactly like mapshaper.org.

Where it looks, in order:
    1. MAPSHAPER_BIN environment variable
    2. PATH (npm install -g mapshaper)
    3. <project>/tools/mapshaper   (e.g. made with: npm install --prefix tools/mapshaper mapshaper)

A path can be the mapshaper executable or the folder that holds it.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
_INSIDE = (
    "mapshaper.cmd",
    "mapshaper",
    "node_modules/.bin/mapshaper.cmd",
    "node_modules/.bin/mapshaper",
)


class MapshaperMissingError(Exception):
    """mapshaper could not be found."""


class MapshaperError(Exception):
    """mapshaper ran and failed. `message` is one short line."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def _resolve(p) -> Path | None:
    path = Path(str(p).strip().strip('"'))
    if path.is_file():
        return path
    for rel in _INSIDE:
        c = path / rel
        if c.is_file():
            return c
    return None


def _candidates():
    env = os.environ.get("MAPSHAPER_BIN", "").strip()
    if env:
        yield env
    on_path = shutil.which("mapshaper")
    if on_path:
        yield on_path
    yield PROJECT_DIR / "tools" / "mapshaper"


def require() -> Path:
    for c in _candidates():
        exe = _resolve(c)
        if exe is not None:
            return exe
    msg = "mapshaper isn't installed. Run: npm install -g mapshaper"
    raise MapshaperMissingError(msg)


def to_topojson(fc: dict | str, name: str) -> str:
    """A GeoJSON FeatureCollection as TopoJSON (one object called `name`), as mapshaper writes it."""
    exe = require()
    with tempfile.TemporaryDirectory(prefix="mapshaper-") as tmp:
        src = Path(tmp) / f"{name}.json"
        out = Path(tmp) / f"{name}.topojson"
        src.write_text(
            fc if isinstance(fc, str) else json.dumps(fc, ensure_ascii=False), encoding="utf-8"
        )
        try:
            p = subprocess.run(
                [str(exe), "-i", str(src), "-o", str(out), "format=topojson"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as e:
            raise MapshaperError(f"Could not start mapshaper: {e}"[:140]) from e
        if p.returncode != 0 or not out.is_file():
            tail = (p.stderr or p.stdout or "").strip().splitlines()
            raise MapshaperError(f"mapshaper stopped: {tail[-1] if tail else 'no message'}"[:150])
        return out.read_text(encoding="utf-8")
