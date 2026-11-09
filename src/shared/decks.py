"""decks.py — Clue decks: the named groups a clue belongs to, and the ids derived from them.

A deck is just a name. Its acronym (first letter of each word) prefixes the ids of the clues created in it:
"My Clues" -> "MC-3f9a...". The list of decks and the last one used live in data/local_settings.json.
"""

import re
import uuid

from src.shared import prefs

DEFAULT_DECK = "Main"
MAX_LEN = 40


def clean(name) -> str:
    return " ".join(str(name or "").split())[:MAX_LEN]


def acronym(name: str) -> str:
    return "".join(w[0] for w in re.findall(r"\w+", clean(name))).upper() or "X"


def new_clue_id(deck: str) -> str:
    return f"{acronym(deck)}-{uuid.uuid4().hex}"


def load() -> list[str]:
    saved = prefs.load().get("decks")
    out = list(dict.fromkeys(c for c in map(clean, saved) if c)) if isinstance(saved, list) else []
    return out or [DEFAULT_DECK]


def last() -> str:
    decks = load()
    name = clean(prefs.load().get("last_deck"))
    return name if name in decks else decks[0]


def use(name) -> str:
    """Make `name` a known deck and the last one used. An empty name means the last one used."""
    deck = clean(name) or last()
    decks = load()
    values: dict = {"last_deck": deck}
    if deck not in decks:
        values["decks"] = [*decks, deck]
    prefs.update(**values)
    return deck


def add(names) -> None:
    """Add the decks that are missing from the list (the last used one is not touched)."""
    have = load()
    new = [c for c in dict.fromkeys(clean(n) for n in names) if c and c not in have]
    if new:
        prefs.update(decks=[*have, *new])
