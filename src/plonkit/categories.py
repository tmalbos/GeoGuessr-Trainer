"""categories.py — The category tag a Plonkit tip gets, and where it applies by default.

Shared by the app's importer (src/api/routers/plonkit_import.py) and
scripts/import_plonkit_clues.py. Keep DEFAULT_LOCATION in step with
frontend/src/pages/Explore/clues/tags.js, and meta_category with autoTags.js (same folder).
"""

import re

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


def _plain(text: str) -> str:
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
    plain = _plain(text)
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
