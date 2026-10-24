"""prefs.py — Small local preferences file (map source, Google API key).

Lives in data/local_settings.json. It holds a secret (the API key): keep it out of git.
"""

import json
import threading
from pathlib import Path

PREFS_FILE = Path(__file__).parents[2] / "data" / "local_settings.json"
_LOCK = threading.Lock()


def load() -> dict:
    try:
        data = json.loads(PREFS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def update(**values) -> dict:
    """Set the given values. A value of None removes that entry."""
    with _LOCK:
        data = load()
        for k, v in values.items():
            if v is None:
                data.pop(k, None)
            else:
                data[k] = v
        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PREFS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(PREFS_FILE)
        return data
