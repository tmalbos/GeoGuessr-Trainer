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
    "Topography": "Anywhere",
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


# Camera / coverage "meta" tips. In this order, the first rule that matches wins, before any other.
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

META_CATEGORIES = frozenset({"Google Car", "Camera Generation", "Coverage", "Copyright", "Camera"})


def meta_category(text: str) -> str | None:
    """The meta category the text says (Google Car, Camera Generation, Coverage, Copyright,
    Camera, in that order of priority), or None.
    """
    if GOOGLE_CAR_RE.search(text):
        return "Google Car"
    if CAMERA_GENERATION_RE.search(text):
        return "Camera Generation"
    if SEASON_RE.search(text) and COVERAGE_RE.search(text):
        return "Coverage"
    if COPYRIGHT_RE.search(text):
        return "Copyright"
    if CAMERA_RE.search(text):
        return "Camera"
    return None


# First match wins. The item-level Plonkit tags are appended to the text before matching.
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
            r"kilomet(?:re|er) markers?|road numbers?|sign backs?)\b"
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
        r"\b(architecture|buildings?|houses?|roofs?|walls?|stone buildings?)\b",
        "Architecture",
    ),
    (r"\b(landscape|hills?|mountains?|terrain|elevation|topography|valleys?)\b", "Topography"),
    (r"\b(vineyards?|agriculture|farmlands?|crops?|fields?)\b", "Agriculture"),
    (r"\b(vegetation|trees?|forests?|plants?|bamboo|palms?)\b", "Vegetation"),
    (r"\bsoil\b", "Soil"),
    (r"\b(brands?|logos?|supermarkets?)\b", "Brand"),
    (r"\bnational parks?\b", "National Parks"),
    (r"\bbiomes?\b", "Biome"),
    (
        (
            r"\b(road markings?|road lines?|lane (?:lines?|markings?)|asphalt|road surface|"
            r"highways?)\b"
        ),
        "Roads",
    ),
    (r"\b(town names?|city names?|place names?)\b", "City Names"),
    (
        r"\b(subdivisions?|provinces?|states?|regions?|parishes?|cantons?|counties?)\b",
        "Subdivision Names",
    ),
]

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
    paragraphs = text.split("\n\n")
    # Camera / coverage meta wins over everything else (see meta_category above).
    meta = meta_category(paragraphs[0])
    if meta:
        return meta
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
    for haystack in (paragraphs[0], text):
        lowered = haystack.lower()
        for pattern, category in CATEGORY_RULES:
            if re.search(pattern, lowered):
                return category
    if re.search(r"\bsigns?\b", text, re.IGNORECASE):
        return "Road Sign"
    return "Infrastructure"
