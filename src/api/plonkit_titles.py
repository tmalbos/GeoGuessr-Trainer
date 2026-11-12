"""plonkit_titles.py — Category fixes and a short title for each Plonkit clue (the import's Skip menu).

classify(text, base_category, country="") -> (category, title)
classify_full(...) -> (category, title, location): location is only set (not None) when a subcategory forces it.

Subcategories (SUBCATEGORIES): when exactly one of them is found in the clue, it decides everything: the category,
a fixed plural title (License Plates, Road Lines, Road Markers, Guardrails, Taxis) and, for some, the location.

Otherwise the title, first rule that gives one wins ("noun" = the category name, or Chevrons / Road Markers /
Snow Poles / Crosswalk Signs / Landscape / Pole / Pole Stickers):
  1. whichever comes first in the text, the first bold phrase or the first link text: 1 word -> "<word> <noun>",
     2-3 words -> the phrase itself, 4+ words -> not used (a phrase with "single" / "row(s)" loses those words first)
     (the second one is tried when the first gives nothing)
  2. the first one or two capitalised words of the first sentence + noun (obvious sentence starters, the country
     name and its demonym skipped)
  3. just the noun
Electricity poles have their own order: a one-word bold phrase, then "<previous word> Poles" (or "Electricity Poles"
at the start of a sentence), then the generic rules.
"""

import re
from typing import NamedTuple

from src.plonkit.categories import META_CATEGORIES

BOLD = re.compile(r"\*\*(.+?)\*\*", re.DOTALL)
LINK = re.compile(r"\[([^\]]+)\]\(https?://[^\s)]+\)")
URL = re.compile(r"https?://\S+")

MAX_TITLE = 40
SKIP = {
    "the", "a", "an", "shown", "this", "these", "those", "there", "it", "its", "in", "on", "at", "of",
    "and", "or", "also", "usually", "often", "most", "some", "all", "many", "any", "they", "you",
    "look", "see", "when", "if", "as", "for", "with", "use", "uses", "used", "has", "have",
    "while", "single", "row", "rows",
}  # fmt: skip
EXCLUDE = {"single", "row", "rows"}  # dropped from a bold / link phrase before it is judged

# Demonyms that do not start like the country's name.
IRREGULAR_DEMONYMS = {
    "france": {"french"}, "spain": {"spanish"}, "netherlands": {"dutch"}, "switzerland": {"swiss"},
    "poland": {"polish"}, "finland": {"finnish"}, "denmark": {"danish"}, "sweden": {"swedish"},
    "turkey": {"turkish"}, "ireland": {"irish"}, "england": {"english"}, "greece": {"greek"},
    "philippines": {"filipino"}, "thailand": {"thai"}, "wales": {"welsh"}, "scotland": {"scottish"},
    "iceland": {"icelandic"}, "israel": {"israeli"}, "croatia": {"croatian"},
}  # fmt: skip


class Sub(NamedTuple):
    """A subcategory: it assigns `category` (and maybe `location`) and gives the clue a fixed title."""

    title: str
    category: str
    pattern: re.Pattern
    location: str | None = None


# Looked for in the first paragraph. Exactly one found -> it decides; several -> the one that comes first
# in the text gives the category and the title is built the normal way.
SUBCATEGORIES = [
    Sub(
        "License Plates",
        "License Plate",
        re.compile(r"\b(?:licen[sc]e|number)\s+plates?\b", re.IGNORECASE),
    ),
    Sub("Road Lines", "Roads", re.compile(r"\broad\s+lines?\b", re.IGNORECASE)),
    Sub(
        "Road Markers",
        "Road Sign",
        re.compile(r"\b(?:road|kilomet(?:re|er))\s+markers?\b", re.IGNORECASE),
        "Rural",
    ),
    Sub("Guardrails", "Infrastructure", re.compile(r"\bguard\s?rails?\b", re.IGNORECASE)),
    Sub("Taxis", "Vehicle", re.compile(r"\btaxis?\b", re.IGNORECASE)),
]

# (pattern, category, noun shown instead of the category, looked for in the whole text?)
# Rules that are not "whole text" only look at the first sentence of the clue.
RULES = [
    (re.compile(r"\bchevrons?\b", re.IGNORECASE), "Road Sign", "Chevrons", True),
    (re.compile(r"\broad markers?\b", re.IGNORECASE), "Road Sign", "Road Markers", True),
    (re.compile(r"\bsnow\s*poles?\b", re.IGNORECASE), "Infrastructure", "Snow Poles", True),
    (re.compile(r"\bcrosswalks?\s+signs?\b", re.IGNORECASE), "Road Sign", "Crosswalk Signs", True),
    (re.compile(r"\blandscapes?\b", re.IGNORECASE), "Landscape", "Landscape", True),
]


def _clean(s: str) -> str:
    return " ".join(re.sub(r"[*_]", "", s).split())


def _plain(text: str) -> str:
    return _clean(URL.sub(" ", LINK.sub(r"\1", text)))


def _links_gone(text: str) -> str:
    """The text with links replaced by their text and bare URLs dropped (paragraphs are kept)."""
    return URL.sub(" ", LINK.sub(r"\1", text))


def _up(s: str) -> str:
    """Straight double quotes and a capital first letter ('“sawdust”' -> '"Sawdust"')."""
    s = s.replace("“", '"').replace("”", '"')
    return re.sub(r"^(\W*)(\w)", lambda m: m.group(1) + m.group(2).upper(), s)


def _title_case(s: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in s.split())


def _runs(pattern: re.Pattern, text: str) -> list[tuple[int, str]]:
    """(position, phrase) of the matches of `pattern`; matches separated only by whitespace are one run."""
    runs: list[list] = []
    end = 0
    for m in pattern.finditer(text):
        if runs and not text[end : m.start()].strip():
            runs[-1][1] += " " + m.group(1)
        else:
            runs.append([m.start(), m.group(1)])
        end = m.end()
    cleaned = [(pos, _clean(phrase)) for pos, phrase in runs]
    return [(pos, phrase) for pos, phrase in cleaned if phrase]


def _marks(text: str) -> list[str]:
    """The first bold run and the first link run, whichever comes first in the text going first."""
    found = [(pos, 0, p) for pos, p in _runs(BOLD, LINK.sub(r"\1", text))[:1]]
    first_link = LINK.search(text)
    links = _runs(LINK, text)[:1]
    if (
        first_link and links
    ):  # no link comes before the first one, so its start is the same flattened
        found.append((first_link.start(), 1, links[0][1]))
    return [p for _, _, p in sorted(found)]


def _with(phrase: str, noun: str) -> str:
    phrase = _up(phrase)
    if noun.lower().rstrip("s") in phrase.lower():
        return phrase
    have = [w.lower().rstrip("s") for w in phrase.split()]
    have_noun = [w.lower().rstrip("s") for w in noun.split()]
    if have_noun[: len(have)] == have or all(w in have_noun for w in have):
        return noun  # the phrase is only part of the noun ("Snow" -> "Snow Poles")
    return f"{phrase} {noun}"


def _from_phrase(phrase: str, noun: str) -> str | None:
    words = phrase.split()
    titled = False
    if len(words) > 3 and any(w.lower().strip(".,;:") in EXCLUDE for w in words):
        words = [w for w in words if w.lower().strip(".,;:") not in EXCLUDE]
        while words and words[0].lower() in SKIP:
            words.pop(0)
        while words and words[-1].lower() in SKIP:
            words.pop()
        titled = True
    n = len(words)
    if n == 1:
        return _with(words[0], noun)
    if 2 <= n <= 3:
        text = " ".join(words)
        return _title_case(text) if titled else _up(text)
    return None


def _from_marks(marks: list[str], noun: str) -> str | None:
    return next((t for t in (_from_phrase(p, noun) for p in marks) if t), None)


def _is_country_word(word: str, country: str) -> bool:
    """The country's own name or its demonym (Argentina -> Argentine, Argentinian...)."""
    w = word.lower()
    for part in re.findall(r"[^\W\d_]+", country.lower()):
        if len(part) < 3:
            continue
        if w in IRREGULAR_DEMONYMS.get(part, ()):
            return True
        stem = part.rstrip("aeiouy") or part
        if w.startswith(stem[:-1] if len(stem) > 5 else stem) and len(w) <= len(part) + 5:
            return True
    return False


def _capitalised(text: str, country: str = "") -> str:
    sentence = re.split(r"(?<=[.!?])\s", _plain(text), maxsplit=1)[0]
    out: list[str] = []
    for raw in sentence.split():
        w = raw.strip(".,;:!?()\"'“”’")
        if w[:1].isupper() and w.lower() not in SKIP and not _is_country_word(w, country):
            out.append(w)
            if len(out) == 2:
                break
        elif out:
            break
    return " ".join(out)


POLE = re.compile(r"\bpoles?\b", re.IGNORECASE)


def _pole_phrase(text: str, country: str) -> str | None:
    """ "<previous word> Poles" for the first "pole(s)" that has a usable word before it, or
    "Electricity Poles" when "pole(s)" starts a sentence.
    """
    plain = re.sub(r"\*+", "", _links_gone(text))
    for m in POLE.finditer(plain):
        before = plain[: m.start()]
        if not before.strip() or re.search(r"[.!?]\s*$", before):
            return "Electricity Poles"
        prev = re.search(r"(\S+)\s+$", before)
        if not prev or not re.fullmatch(r"[^\W\d_][\w-]*", prev.group(1)):
            continue  # nothing before it, or a punctuation mark (a clause break) in the way
        word = prev.group(1)
        starts = not before[: prev.start()].strip() or re.search(
            r"[.!?]\s*$", before[: prev.start()]
        )
        if (
            word.lower() in SKIP
            or _is_country_word(word, country)
            or (word[0].isupper() and not starts)
        ):
            continue
        return f"{_up(word)} {m.group(0).capitalize()}"
    return None


def _pole_title(text: str, marks: list[str], cap: str, country: str) -> str:
    """Electricity poles: stickers are their own thing; otherwise a one-word bold phrase, then the word
    before "pole(s)", then the generic rules with "Pole" as the noun.
    """
    if re.search(r"\bstickers?\b", _links_gone(text), re.IGNORECASE):
        noun = "Pole Stickers"
    else:
        noun = "Pole"
        if marks and len(marks[0].split()) == 1:
            return _with(marks[0], noun)
        phrase = _pole_phrase(text, country)
        if phrase:
            return phrase
    return _from_marks(marks, noun) or (_with(cap, noun) if cap else None) or noun


def classify_full(text: str, base: str, country: str = "") -> tuple[str, str, str | None]:
    """(category, title, location). `location` is None unless a subcategory forces one."""
    text = text or ""
    category, noun, location = base, None, None
    # The meta categories (Google Car, Camera Generation...) are never overridden by the rules below.
    if base not in META_CATEGORIES:
        main = _links_gone(text).split("\n\n")[0]
        subs = sorted(
            (m.start(), i) for i, s in enumerate(SUBCATEGORIES) if (m := s.pattern.search(main))
        )
        if subs:
            sub = SUBCATEGORIES[subs[0][1]]
            category, location = sub.category, sub.location
            if len(subs) == 1:
                return category, sub.title, location
        else:
            first = re.split(r"(?<=[.!?])\s", _plain(text), maxsplit=1)[0]
            for rx, cat, shown, whole in RULES:
                if rx.search(text if whole else first):
                    category, noun = cat, shown
                    break
    noun = noun or category
    cap = _capitalised(text, country)
    marks = _marks(text)
    if category == "Electricity Pole":
        title = _pole_title(text, marks, cap, country)
    else:
        title = _from_marks(marks, noun) or (_with(cap, noun) if cap else None) or noun
    if category == "Roads" and re.search(r"\bpavements?\b", text, re.IGNORECASE):
        title = _title_case(title)
    return category, title[:MAX_TITLE], location


def classify(text: str, base: str, country: str = "") -> tuple[str, str]:
    category, title, _ = classify_full(text, base, country)
    return category, title
