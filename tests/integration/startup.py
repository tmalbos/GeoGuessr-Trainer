"""Integration test — verifies python main.py starts without crashing."""

import os
import subprocess
import sys

import pytest


def test_application_starts_without_crashing() -> None:
    """Python main.py must reach the interactive menu without raising during startup."""
    env = {**os.environ, "PYTHONUTF8": "1"}
    proc = subprocess.Popen(
        [sys.executable, "main.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(input="", timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        pytest.fail("main.py did not complete startup within 15 seconds")

    assert "GeoGuessr Analyzer" in stdout, (
        f"Startup failed — welcome banner not printed.\nstdout:\n{stdout}\nstderr:\n{stderr}"
    )
