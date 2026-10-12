"""admin_terms.py — What each country calls its first-level administrative division.

Used by the replay report to say "Wakiso district", "Texas state", "Ontario province"...
Unlisted countries fall back to DEFAULT_TERM. Edit the groups below to fix a country.
"""

UNKNOWN_PLACE = "Unknown_Country"  # shown when a point isn't covered by the local geo layers
DEFAULT_TERM = "region"

# term -> ISO alpha-2 codes (space separated)
_GROUPS = {
    "state": "AU AT BR DE FM IN MX MY NG SD SS SO US VE",
    "province": (
        "AF AO AR AM BE BG BI CA CD CN CR CU DO DZ EC GA GQ ID IR KH KP KR LA LK MN MZ NL NP "
        "PA PG PH PK RW SB SL TH TR VN VU ZA ZM ZW"
    ),
    "region": (
        "BF CL CM DK ET ER DJ FI FR GH GN GR GW GY IS IT KZ MA MG ML MM MR MW NA NE NZ PE RU "
        "SA SN SK CZ SI SZ TD TG TJ TM TZ TT UZ"
    ),
    "district": "UG BW BZ BT BN BS CI IL LS MD PT RS SR WS CY AZ LY",
    "county": "KE LR HR HU NO RO SE IE EE LT AL TW",
    "department": "BO CO CG UY PY SV HN GT NI HT BJ",
    "governorate": "EG IQ JO SY LB YE OM KW BH TN PS",
    "oblast": "UA BY KG",
    "prefecture": "JP",
    "voivodeship": "PL",
    "canton": "CH LU",
    "parish": "JM BB AG DM KN",
    "emirate": "AE",
    "country": "GB",
    "autonomous community": "ES",
    "municipality": "TL CV ME QA LI",
    "division": "BD FJ TO",
}

TERMS: dict[str, str] = {c: term for term, codes in _GROUPS.items() for c in codes.split()}

_NO_SUFFIX = {"country"}  # "England country" reads badly: leave the name alone


def admin_term(country_code: str) -> str:
    return TERMS.get((country_code or "").strip().upper(), DEFAULT_TERM)


def with_term(name: str, country_code: str) -> str:
    """'Wakiso' + UG -> 'Wakiso district'. Names that already carry the word are left alone."""
    if not name:
        return ""
    term = admin_term(country_code)
    if term in _NO_SUFFIX or term in name.lower():
        return name
    return f"{name} {term}"
