import json
import subprocess
import sys

data = json.load(sys.stdin)
file_path = data.get("tool_input", {}).get("file_path", "")

if not file_path.endswith(".py"):
    sys.exit(0)

subprocess.run(["ruff", "format", file_path], capture_output=True)
result = subprocess.run(
    ["ruff", "check", "--preview", "--fix", file_path],
    capture_output=True,
    text=True,
)

if result.returncode != 0:
    errors = result.stdout.strip() or result.stderr.strip()
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PostToolUse",
                    "additionalContext": f"ruff check found unfixable issues in {file_path}:\n{errors}",
                },
            },
        ),
    )
