"""geo_levels.py — The geographic hierarchy shared by analysis, the API and the CLI."""

GEO_LEVELS = [
    ("general", "General"),
    ("continent", "Continent"),
    ("biome", "Biome"),
    ("country", "Country"),
]


CHILD_LEVEL = {
    "general": None,
    "continent": "country",
    "biome": None,
    "country": "state",
}
