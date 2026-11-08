"""plonkit_titles.py — Category fixes and a short title for each Plonkit clue (the import's Skip menu).

classify(text, base_category) -> (category, title)

Title, first rule that gives one wins ("noun" = the category name, or Chevron / Road Marker / Landscape):
  1. the first bold phrase: 1 word -> "<word> <noun>", 2-3 words -> the phrase itself, 4+ words -> not used
  2. the same with the first link text
  3. the first one or two capitalised words of the first sentence + noun (obvious sentence starters skipped)
  4. just the noun
"""

import re

BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
LINK = re.compile(r"\[([^\]]+)\]\(https?://[^\s)]+\)")
URL = re.compile(r"https?://\S+")

MAX_TITLE = 40
SKIP = {
    "the", "a", "an", "shown", "this", "these", "those", "there", "it", "its", "in", "on", "at", "of",
    "and", "or", "also", "usually", "often", "most", "some", "all", "many", "any", "they", "you",
    "look", "see", "when", "if", "as", "for", "with", "use", "uses", "used", "has", "have",
}  # fmt: skip

# (pattern, category, noun shown instead of the category, looked for in the whole text?)
# Rules that are not "whole text" only look at the first sentence of the clue.
RULES = [
    (re.compile(r"\bchevrons?\b", re.IGNORECASE), "Road Sign", "Chevron", True),
    (re.compile(r"\broad markers?\b", re.IGNORECASE), "Road Sign", "Road Marker", True),
    (re.compile(r"\blandscapes?\b", re.IGNORECASE), "Topography", "Landscape", True),
    (re.compile(r"\b(?:soil|dirt)\b", re.IGNORECASE), "Soil", None, False),
    (
        re.compile(r"\b(?:shrubs?|succulents?|palms?|trees?)\b", re.IGNORECASE),
        "Vegetation",
        None,
        False,
    ),
]


def _clean(s: str) -> str:
    return " ".join(re.sub(r"[*_]", "", s).split())


def _plain(text: str) -> str:
    return _clean(URL.sub(" ", LINK.sub(r"\1", text)))


def _runs(pattern: re.Pattern, text: str) -> list[str]:
    """Matches of `pattern`; matches separated only by whitespace count as one continuous run."""
    runs: list[str] = []
    end = 0
    for m in pattern.finditer(text):
        if runs and not text[end : m.start()].strip():
            runs[-1] += " " + m.group(1)
        else:
            runs.append(m.group(1))
        end = m.end()
    return [r for r in (_clean(x) for x in runs) if r]


def _with(phrase: str, noun: str) -> str:
    phrase = phrase[:1].upper() + phrase[1:]
    return phrase if noun.lower().rstrip("s") in phrase.lower() else f"{phrase} {noun}"


def _from_run(runs: list[str], noun: str) -> str | None:
    if not runs:
        return None
    n = len(runs[0].split())
    if n == 1:
        return _with(runs[0], noun)
    return runs[0][:1].upper() + runs[0][1:] if 2 <= n <= 3 else None


def _capitalised(text: str) -> str:
    sentence = re.split(r"(?<=[.!?])\s", _plain(text), maxsplit=1)[0]
    out: list[str] = []
    for raw in sentence.split():
        w = raw.strip(".,;:!?()\"'“”’")
        if w[:1].isupper() and w.lower() not in SKIP:
            out.append(w)
            if len(out) == 2:
                break
        elif out:
            break
    return " ".join(out)


def classify(text: str, base: str) -> tuple[str, str]:
    text = text or ""
    bold = _runs(BOLD, LINK.sub(r"\1", text))
    links = _runs(LINK, text)
    first = re.split(r"(?<=[.!?])\s", _plain(text), maxsplit=1)[0]
    category, noun = base, None
    for rx, cat, shown, whole in RULES:
        if rx.search(text if whole else first):
            category, noun = cat, shown
            break
    noun = noun or category
    cap = _capitalised(text)
    title = (
        _from_run(bold, noun)
        or _from_run(links, noun)
        or (_with(cap, noun) if cap else None)
        or noun
    )
    return category, title[:MAX_TITLE]
