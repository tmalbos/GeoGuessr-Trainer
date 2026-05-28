"""PostToolUse hook: run unit tests after editing project Python/HTML files."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

project_root = os.path.normcase(os.path.normpath(Path(__file__).parent.parent))

try:
    hook_input = json.load(sys.stdin)
except ValueError:
    hook_input = {}

raw = hook_input.get("tool_input", {}).get("file_path", "")
file_path = os.path.normcase(os.path.normpath(raw)) if raw else ""


def should_run(path: str) -> bool:
    """Check whether unit tests should run for the given file path.

    Returns
    -------
    bool
        True if the path is inside the project and is a .py or .html file.

    """
    if not path.startswith(project_root + os.sep):
        return False
    return path.endswith((".py", ".html"))


if not should_run(file_path):
    sys.exit(0)

result = subprocess.run(
    [
        sys.executable,
        "-m",
        "pytest",
        "tests/unit",
        "--override-ini=addopts=",
        "-q",
        "--tb=no",
    ],
    capture_output=True,
    text=True,
    check=False,
)

output = result.stdout + result.stderr
failed = re.findall(r"^FAILED\s+(\S+)", output, re.MULTILINE)

if not failed:
    message = "All tests passed!"
else:
    lines = ["These tests FAILED:"] + [f"  - {f}" for f in failed]
    message = "\n".join(lines)

print(
    json.dumps(
        {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": message,
            },
        },
    ),
)
