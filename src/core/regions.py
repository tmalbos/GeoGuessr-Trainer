# regions.py
# (country_code_nominatim, state_nominatim) -> cca2 real de la región
# Solo necesitamos el cca2; REST Countries /alpha/{cca2} hace el resto.

REGION_OVERRIDES: dict[tuple[str, str], str] = {
    # China
    ("CN", "Hong Kong"): "HK",
    ("CN", "Macau"): "MO",
    # Taiwan
    ("CN", "Taiwan"): "TW",
    # United States
    ("US", "Puerto Rico"): "PR",
    ("US", "Guam"): "GU",
    ("US", "American Samoa"): "AS",
    ("US", "United States Virgin Islands"): "VI",
    ("US", "Northern Mariana Islands"): "MP",
    # United Kingdom
    ("GB", "Gibraltar"): "GI",
    ("GB", "Isle of Man"): "IM",
    ("GB", "Jersey"): "JE",
    # Denmark
    ("DK", "Faroe Islands"): "FO",
    ("DK", "Greenland"): "GL",
    # France
    ("FR", "Martinique"): "MQ",
    ("FR", "Réunion"): "RE",
    ("FR", "Saint Pierre and Miquelon"): "PM",
    # Norway
    ("NO", "Svalbard"): "SJ",
    # Australia
    ("AU", "Christmas Island"): "CX",
    ("AU", "Cocos Islands"): "CC",
    # New Zealand
    ("NZ", "Pitcairn Islands"): "PN",
    # Special cases of partial recognition
    ("IL", "Palestinian Territory"): "PS",
}


def get_override(country_code: str, state: str) -> str | None:
    """Devuelve el cca2 de la región si corresponde un override,
    None si hay que tratar el país normalmente.
    """
    return REGION_OVERRIDES.get((country_code.upper(), state))
