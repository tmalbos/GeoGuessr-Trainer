"""clue_titles.py — the category and short title a Plonkit clue gets."""

import re
from typing import NamedTuple

DEFAULT_LOCATION = {
    "Architecture": "Urban",
    "Phone Numbers": "Urban",
    "Postal Codes": "Urban",
    "Landscape": "Anywhere",
    "Vegetation": "Rural",
    "Infrastructure": "Anywhere",
    "Vehicle": "Anywhere",
    "License Plate": "Anywhere",
    "Flags": "Urban",
    "Roads": "Anywhere",
    "Subdivision Names": "Anywhere",
    "City Names": "Anywhere",
    "Agriculture": "Rural",
    "Soil": "Rural",
    "Brand": "Urban",
    "Language": "Anywhere",
    "Script": "Anywhere",
    "Road Sign": "Anywhere",
    "Electricity Pole": "Anywhere",
    "Bollard": "Rural",
    "National Parks": "Rural",
    "Biome": "Anywhere",
    "Religion": "Urban",
    "Animals": "Rural",
    "Google Car": "Anywhere",
    "Copyright": "Anywhere",
    "Camera Generation": "Anywhere",
    "Camera": "Anywhere",
    "Coverage": "Anywhere",
}


# Camera / coverage "meta" tips. They compete with every other rule: the tag whose trigger word
# is closest to the start of the text wins (see category_for). On a tie, these come first.
GOOGLE_CAR_RE = re.compile(
    r"\b(?:google car|street view car|snorkel|roof ?rack|antenn?as?)\b", re.IGNORECASE
)
CAMERA_GENERATION_RE = re.compile(r"\b(?:camera\s+)?gen(?:eration)?\.?\s*[1-4]\b", re.IGNORECASE)
SEASON_RE = re.compile(
    r"\b(?:snow\w*|winter\w*|summer\w*|spring\w*|autumn\w*|fall)\b", re.IGNORECASE
)
COVERAGE_RE = re.compile(r"\bcoverage\b", re.IGNORECASE)
COPYRIGHT_RE = re.compile(r"©|\bcopyright\b", re.IGNORECASE)
CAMERA_RE = re.compile(r"\b(?:smudges?|rifts?|dead pixels?)\b", re.IGNORECASE)
TREKKER_RE = re.compile(r"\btrekkers?\b", re.IGNORECASE)
CAR_RE = re.compile(r"\bcar\b", re.IGNORECASE)
BACK_RE = re.compile(r"\bback\b", re.IGNORECASE)

META_CATEGORIES = frozenset({"Google Car", "Camera Generation", "Coverage", "Copyright", "Camera"})


def _first(*matches: re.Match | None) -> int | None:
    """Start of the earliest of the given matches (None entries are ignored)."""
    starts = [m.start() for m in matches if m]
    return min(starts) if starts else None


def meta_hits(text: str) -> list[tuple[int, int, str]]:
    """Every meta category the text says, as (position, order, category).

    Google Car also fires when the words "car" and "back" are both there; Coverage also fires
    on a trekker.
    """
    season, coverage = SEASON_RE.search(text), COVERAGE_RE.search(text)
    car, back = CAR_RE.search(text), BACK_RE.search(text)
    found = [
        ("Google Car", _first(GOOGLE_CAR_RE.search(text), *((car, back) if car and back else ()))),
        ("Camera Generation", _first(CAMERA_GENERATION_RE.search(text))),
        (
            "Coverage",
            _first(*((season, coverage) if season and coverage else ()), TREKKER_RE.search(text)),
        ),
        ("Copyright", _first(COPYRIGHT_RE.search(text))),
        ("Camera", _first(CAMERA_RE.search(text))),
    ]
    return [(pos, i, cat) for i, (cat, pos) in enumerate(found) if pos is not None]


def meta_category(text: str) -> str | None:
    """The meta category (Google Car, Camera Generation, Coverage, Copyright, Camera) whose
    trigger is closest to the start of the text, or None.
    """
    hits = meta_hits(text)
    return min(hits)[2] if hits else None


# Every rule is tried; the one whose trigger sits closest to the start of the text wins (a tie
# goes to the rule listed first). The item-level Plonkit tags are appended to the text before matching.
CATEGORY_RULES: list[tuple[str, str]] = [
    (r"\b(license|licence|number)?\s*plates?\b", "License Plate"),
    (r"\bbollards?\b", "Bollard"),
    (r"\b(electricity|power)\s+(pole|pylon)s?\b|\bpoletops?\b|\bpoles?\b", "Electricity Pole"),
    (
        (
            r"\b(cyrillic|arabic|georgian|greek|hangul|thai|khmer|lao|hebrew|devanagari|"
            r"bengali|tamil|sinhala|burmese|japanese|chinese|korean)\s+"
            r"(script|alphabet|characters|writing)\b|\bwritten in\s+"
            r"(cyrillic|arabic|georgian|greek|hangul|thai|khmer|lao|hebrew|devanagari|"
            r"bengali|tamil|sinhala|burmese|japanese|chinese|korean)\b"
        ),
        "Script",
    ),
    (
        (
            r"\b(multilingual|languages?|alphabets?|closely related to [a-z]+|words? for|"
            r"speaks?|umlauts?)\b"
        ),
        "Language",
    ),
    (
        (
            r"\b(chevrons?|road signs?|street signs?|signposts?|directional signs?|"
            r"give[ -]?way signs?|yield signs?|speed limit signs?|green signs?|"
            r"town entrance signs?|pedestrian signs?|bus stop signs?|traffic signs?|"
            r"kilomet(?:re|er) markers?|road markers?|road numbers?|sign backs?)\b"
        ),
        "Road Sign",
    ),
    (r"\b(guardrails?|guard rails?|crash barriers?|frames?|traffic cones?)\b", "Infrastructure"),
    (r"\bflags?\b", "Flags"),
    (r"\b(phone numbers?|area codes?|dialling codes?)\b", "Phone Numbers"),
    (r"\b(postal codes?|zip codes?)\b", "Postal Codes"),
    (r"\b(church|churches|cathedral|mosque|temple|religion)\b", "Religion"),
    (
        (
            r"\b(national banks?|food chains?|fast food|telecom(?:munications?)?|"
            r"gas stations?|petrol stations?|fuel stations?|beers?|breweries?|"
            r"post(?:al)? offices?)\b"
        ),
        "Brand",
    ),
    (
        r"\b(architecture|buildings?|houses?|roofs?|walls?|stone buildings?|bricks?)\b",
        "Architecture",
    ),
    (
        (
            r"\b(landscapes?|hills?|mountains?|terrain|elevation|topography|valleys?|"
            r"coast(?:al)?|grassland|lakes?|rocks?|salt pans?)\b"
        ),
        "Landscape",
    ),
    (r"\b(vineyards?|agriculture|farmlands?|crops?|fields?|plantations?)\b", "Agriculture"),
    (
        (
            r"\b(vegetation|trees?|forests?|plants?|bamboo|palms?|cactus|cacti|"
            r"sunflowers?|grass|flora)\b"
        ),
        "Vegetation",
    ),
    (r"\bsoil\b", "Soil"),
    (r"\b(brands?|logos?|supermarkets?)\b", "Brand"),
    (r"\bnational parks?\b", "National Parks"),
    (r"\bbiomes?\b", "Biome"),
    (
        (
            r"\b(road markings?|road lines?|lane (?:lines?|markings?)|asphalt|road surface|"
            r"highways?|pavements?)\b"
        ),
        "Roads",
    ),
    (r"\b(town names?|city names?|place names?)\b", "City Names"),
    (
        r"\b(subdivisions?|provinces?|states?|regions?|parishes?|cantons?|counties?)\b",
        "Subdivision Names",
    ),
]

# Rules that only say where something is, not what it is: used only when nothing else matches.
WEAK_CATEGORIES = frozenset({"Subdivision Names"})

LINK_RE = re.compile(r"\[([^\]]+)\]\(https?://[^\s)]+\)")
URL_RE = re.compile(r"https?://\S+")


def _without_links(text: str) -> str:
    """The text without link targets / bare URLs (their words must not trigger anything)."""
    return URL_RE.sub(" ", LINK_RE.sub(r"\1", text))


def _best(text: str, meta: bool = True) -> str | None:
    """The category whose trigger is closest to the start of `text`.

    Ties go to the meta tags, then to the order of CATEGORY_RULES. Weak categories only count
    when nothing else matched.
    """
    hits = meta_hits(text) if meta else []
    lowered = text.lower()
    for i, (pattern, category) in enumerate(CATEGORY_RULES, start=5):
        m = re.search(pattern, lowered)
        if m:
            hits.append((m.start(), i, category))
    pool = [h for h in hits if h[2] not in WEAK_CATEGORIES] or hits
    return min(pool)[2] if pool else None


PLONKIT_TAG_CATEGORY = {
    "bollard": "Bollard",
    "pole": "Electricity Pole",
    "guardrail": "Infrastructure",
    "chevron": "Road Sign",
    "sign": "Road Sign",
    "signs": "Road Sign",
}


def category_for(text: str, plonkit_tags: list[str]) -> str:
    """Classify a tip. The first paragraph is the main claim; NOTES often mention other things."""
    plain = _without_links(text)
    paragraphs = plain.split("\n\n")
    best = _best(paragraphs[0])
    # A camera / coverage tag wins when its trigger is the closest one to the start of the text.
    if best in META_CATEGORIES:
        return best
    # Plonkit tags roadside snow poles as "pole", but they are edge markers rather than
    # electricity poles. Check the text before trusting that tag.
    if re.search(r"\bsnow\s*poles?\b", paragraphs[0], re.IGNORECASE):
        return "Infrastructure"
    # Plonkit sometimes tags bollards as "pole" (or lists both tags). Bollards are not
    # electricity poles, so trust the text first.
    if re.search(r"\bbollards?\b", paragraphs[0], re.IGNORECASE):
        return "Bollard"
    # A bus stop can have a "white plate" with numbers; that is not a licence plate.
    if re.search(r"\bbus stops?\b", paragraphs[0], re.IGNORECASE):
        return "Road Sign"
    for tag in plonkit_tags:
        for part in tag.lower().split("/"):
            mapped = PLONKIT_TAG_CATEGORY.get(part)
            if mapped:
                return mapped
    best = best or _best(plain, meta=False)
    if best:
        return best
    if re.search(r"\bsigns?\b", plain, re.IGNORECASE):
        return "Road Sign"
    return "Infrastructure"


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
