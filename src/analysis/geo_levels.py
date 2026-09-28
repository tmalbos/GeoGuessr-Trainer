"""geo_levels.py — The geographic hierarchy shared by analysis, the API and the CLI."""

GEO_LEVELS = [
    ("general", "General"),
    ("realm", "Realm"),
    ("continent", "Continent"),
    ("biome", "Biome"),
    ("country", "Country"),
    ("ecoregion", "Ecoregion"),
]


CHILD_LEVEL = {
    "general": None,
    "realm": "biome",
    "continent": "country",
    "biome": "ecoregion",
    "country": "state",
    "ecoregion": None,
}
