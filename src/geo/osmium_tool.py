r"""osmium_tool.py — Find and run osmium-tool (the C++ command line), used by the OSM extractor.

Where it looks, in order:
    1. OSMIUM_BIN environment variable
    2. the path saved from the Split card (data/local_settings.json, key "osmium_bin")
    3. PATH
    4. <project>/tools/osmium   (e.g. made with: micromamba create -p tools\\osmium -c conda-forge osmium-tool)

A path can be the osmium executable or the folder that holds it (conda puts it in Library\\bin on Windows).
"""

import os
import shutil
import subprocess
from pathlib import Path

from src.shared import prefs

PROJECT_DIR = Path(__file__).resolve().parents[2]
_INSIDE = ("osmium.exe", "Library/bin/osmium.exe", "bin/osmium", "osmium")


class OsmiumMissingError(Exception):
    """osmium-tool could not be found."""


class OsmiumError(Exception):
    """osmium ran and failed. `message` is one short line, `detail` the tail of its output."""

    def __init__(self, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail


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
    env = os.environ.get("OSMIUM_BIN", "").strip()
    if env:
        yield env
    saved = str(prefs.load().get("osmium_bin") or "").strip()
    if saved:
        yield saved
    on_path = shutil.which("osmium")
    if on_path:
        yield on_path
    yield PROJECT_DIR / "tools" / "osmium"


def find() -> Path | None:
    for c in _candidates():
        exe = _resolve(c)
        if exe is not None:
            return exe
    return None


def require() -> Path:
    exe = find()
    if exe is None:
        msg = "osmium-tool isn't set up yet. Follow the steps in the Split card."
        raise OsmiumMissingError(msg)
    return exe


def run(args: list[str]) -> str:
    """Run `osmium <args>` and return stdout. Raises OsmiumError on a non-zero exit."""
    exe = require()
    env = {**os.environ, "PATH": str(exe.parent) + os.pathsep + os.environ.get("PATH", "")}
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        p = subprocess.run(
            [str(exe), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            check=False,
            creationflags=flags,
        )
    except OSError as e:
        raise OsmiumError(f"Could not start osmium: {e}"[:140]) from e
    if p.returncode != 0:
        tail = (p.stderr or p.stdout or "").strip().splitlines()
        last = tail[-1] if tail else "no message"
        raise OsmiumError(f"osmium stopped: {last}"[:150], "\n".join(tail[-15:]))
    return p.stdout


def status() -> dict:
    exe = find()
    if exe is None:
        return {"found": False, "path": "", "version": ""}
    try:
        version = (run(["--version"]).strip().splitlines() or [""])[0]
    except (OsmiumError, OsmiumMissingError):
        return {"found": False, "path": str(exe), "version": ""}
    return {"found": True, "path": str(exe), "version": version}


def save(path: str) -> dict:
    """Remember a path (empty = forget it) and return the new status."""
    text = (path or "").strip()
    if not text:
        prefs.update(osmium_bin=None)
    else:
        exe = _resolve(text)
        if exe is None:
            msg = "No osmium.exe found at that path."
            raise OsmiumMissingError(msg)
        prefs.update(osmium_bin=str(exe))
    return status()
