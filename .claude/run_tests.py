import json
import os
import re
import subprocess
import sys
from pathlib import Path

project_root = os.path.normcase(os.path.normpath(Path(__file__).parent.parent))

try:
    hook_input = json.load(sys.stdin)
except Exception:
    hook_input = {}

raw = hook_input.get("tool_input", {}).get("file_path", "")
file_path = os.path.normcase(os.path.normpath(raw)) if raw else ""


def should_run(path: str) -> bool:
    if not path.startswith(project_root + os.sep):
        return False
    rel = path[len(project_root) + 1 :]
    parts = Path(rel).parts
    if not parts:
        return False
    return parts[0] in {"src", "tests"} or (len(parts) == 1 and rel.endswith(".py"))


if not should_run(file_path):  # type: ignore[arg-type]
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
)

output = result.stdout + result.stderr
failed = re.findall(r"^FAILED\s+(\S+)", output, re.MULTILINE)

if not failed and result.returncode == 0:
    message = "All tests passed!"
elif not failed:
    last_line = output.strip().split("\n")[-1] if output.strip() else "unknown error"
    message = f"Test run error (no tests collected or crash): {last_line}"
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
        }
    )
)
