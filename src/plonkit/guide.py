"""guide.py — Read a Plonkit guide page and clean its text.

Shared by the app's importer (src/api/routers/plonkit_import.py) and
scripts/import_plonkit_clues.py.
"""

import json
import re

BASE = "https://www.plonkit.net"
SPACES_RE = re.compile(r"[ \t]+")


def read_guide(html: str) -> dict:
    """Pull the embedded guide JSON out of a Plonkit page."""
    match = re.search(
        r'<script id="__PRELOADED_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        msg = "This page has no __PRELOADED_DATA__ block (is it a Plonkit guide?)"
        raise ValueError(msg)
    payload = json.loads(match.group(1))
    guide = payload.get("data", {}).get("public")
    if not guide:
        msg = "The page did not contain a public guide"
        raise ValueError(msg)
    return guide


def national_step(guide: dict) -> dict:
    """Return the first step, which is Plonkit's national 'Identifying …' section."""
    steps = guide.get("steps") or []
    if not steps:
        msg = "This guide has no steps"
        raise ValueError(msg)
    first = steps[0]
    title = (first.get("title") or "").lower()
    if "identifying" not in title:
        msg = f"First step is {first.get('title')!r}, not an 'Identifying ...' section"
        raise ValueError(msg)
    return first


def clean_text(paragraphs: list[str]) -> str:
    """Join Plonkit's paragraphs with blank lines and repair its markdown quirks."""
    out: list[str] = []
    for raw in paragraphs:
        text = raw.strip().replace("\u00a0", " ")
        text = SPACES_RE.sub(" ", text)
        # Relative guide links (e.g. an infographic) stay usable in the clue card.
        text = re.sub(r"\]\((/(?!/)[^\s)]+)\)", rf"]({BASE}\1)", text)
        # Plonkit sometimes leaves a space before the closing ** ("**Town signs **are").
        text = re.sub(r"\*\*([^*\n]+?)\s+\*\*(?=\w)", r"**\1** ", text)
        text = re.sub(r"\*\*([^*\n]+?)\s+\*\*", r"**\1**", text)
        if text:
            out.append(text)
    return "\n\n".join(out)
