"""Shared constants for country_to_svg_v2.

Kept in one place because the same values (colors, tolerances, the output
canvas size) are each used from more than one of the other modules, and
duplicating them would risk the copies drifting apart.
"""

# ---- fixed style constants ----
COUNTRIES_COLORS = {
    "Albania": {"water_color": "#87D8EB", "land_color": "#CFF6DF"},
    "Argentina": {"water_color": "#75D3E7", "land_color": "#BAEBD6"},
    "Australia": {"water_color": "#76D3E7", "land_color": "#FBF8F2"},
    "Bangladesh": {"water_color": "#84D7EB", "land_color": "#CCF4DF"},
    "Belgium": {"water_color": "#88D8EB", "land_color": "#CFF6E1"},
    "Bolivia": {"water_color": "#7ED5E9", "land_color": "#B2EBCF"},
    "Botswana": {"water_color": "#81D5E9", "land_color": "#F4EFE3"},
    "Brazil": {"water_color": "#77D3E7", "land_color": "#BAEBD6"},
    "Bulgaria": {"water_color": "#87D7EB", "land_color": "#C1F2D6"},
    "Cambodia": {"water_color": "#87D8EB", "land_color": "#BFF2D6"},
    "Canada": {"water_color": "#6DD1E5", "land_color": "#A6E5CA"},
    "Chile": {"water_color": "#74D3E7", "land_color": "#BAEBD6"},
    "Colombia": {"water_color": "#7CD5E9", "land_color": "#B0EACF"},
    "CostaRica": {"water_color": "#88D8EB", "land_color": "#C3F3D8"},
    "Cyprus": {"water_color": "#90D9ED", "land_color": "#F4EFE3"},
    "Czechia": {"water_color": "#87D7EB", "land_color": "#CFF6DF"},
    "Denmark": {"water_color": "#84D7EB", "land_color": "#CDF5DF"},
    "Ecuador": {"water_color": "#83D6EB", "land_color": "#A9EBC6"},
    "Estonia": {"water_color": "#87D7EB", "land_color": "#B0EFCA"},
    "Eswatini": {"water_color": "#8DD8ED", "land_color": "#D1F7E1"},
    "FaroeIslands": {"water_color": "#8BD8ED", "land_color": "#DDDDD4"},
    "France": {"water_color": "#7ED5E9", "land_color": "#C4F0DA"},
    "Georgia": {"water_color": "#77D3E7", "land_color": "#BAEBD6"},
    "Ghana": {"water_color": "#83D6EB", "land_color": "#CAF4DD"},
    "Greece": {"water_color": "#82D6EB", "land_color": "#C9F3DD"},
    "Hungary": {"water_color": "#86D7EB", "land_color": "#CFF6DF"},
    "Iceland": {"water_color": "#82D6EB", "land_color": "#C9F3DD"},
    "India": {"water_color": "#79D4E9", "land_color": "#BEEDD8"},
    "Indonesia": {"water_color": "#7AD4E9", "land_color": "#96E3BE"},
    "Ireland": {"water_color": "#84D7EB", "land_color": "#BBEFD1"},
    "Italy": {"water_color": "#7ED5E9", "land_color": "#C3EFDA"},
    "Japan": {"water_color": "#7AD4E9", "land_color": "#98E3C1"},
    "Jordan": {"water_color": "#86D7EB", "land_color": "#F7F3EB"},
    "Kazakhstan": {"water_color": "#7AD4E9", "land_color": "#F4EFE3"},
    "Kenya": {"water_color": "#77D3E7", "land_color": "#F4EFE3"},
    "Kyrgyzstan": {"water_color": "#84D7EB", "land_color": "#CDF5DF"},
    "Latvia": {"water_color": "#86D7EB", "land_color": "#B0EFCA"},
    "Luxembourg": {"water_color": "#91D1E3", "land_color": "#CAEDD8"},
    "Malaysia": {"water_color": "#81D5E9", "land_color": "#A3E9C6"},
    "Mexico": {"water_color": "#77D3E7", "land_color": "#BAEBD6"},
    "Namibia": {"water_color": "#7FD5E9", "land_color": "#F4EFE3"},
    "Netherlands": {"water_color": "#77D3E7", "land_color": "#BAEBD6"},
    "NewZealand": {"water_color": "#7CD5E9", "land_color": "#B0EACF"},
    "Nigeria": {"water_color": "#80D5E9", "land_color": "#F4EFE3"},
    "Norway": {"water_color": "#78D4E9", "land_color": "#A9E7CA"},
    "Oman": {"water_color": "#81D5E9", "land_color": "#FAF7F2"},
    "Peru": {"water_color": "#7CD5E9", "land_color": "#9BE5C1"},
    "Philippines": {"water_color": "#7ED5E9", "land_color": "#B2ECCF"},
    "Poland": {"water_color": "#81D5E9", "land_color": "#C8F2DD"},
    "Portugal": {"water_color": "#83D6EB", "land_color": "#CAF4DD"},
    "PuertoRico": {"water_color": "#90D9ED", "land_color": "#D3F8E1"},
    "Romania": {"water_color": "#83D6EB", "land_color": "#CCF4DD"},
    "Russia": {"water_color": "#6ED1E5", "land_color": "#A6E5CA"},
    "Senegal": {"water_color": "#86D7EB", "land_color": "#F4EFE3"},
    "Serbia": {"water_color": "#85D7EB", "land_color": "#CEF5DF"},
    "Slovenia": {"water_color": "#8BD8ED", "land_color": "#C4F4D6"},
    "SouthAfrica": {"water_color": "#7ED5E9", "land_color": "#F4EFE3"},
    "SouthKorea": {"water_color": "#84D6EB", "land_color": "#BAEECF"},
    "Spain": {"water_color": "#7FD5E9", "land_color": "#C6F1DA"},
    "SriLanka": {"water_color": "#87D7EB", "land_color": "#C1F2D6"},
    "Sweden": {"water_color": "#78D4E9", "land_color": "#A9E6CA"},
    "Switzerland": {"water_color": "#89D8EB", "land_color": "#C6F3DA"},
    "Taiwan": {"water_color": "#88D8EB", "land_color": "#B8F0CF"},
    "Tunisia": {"water_color": "#82D6EB", "land_color": "#FAF6F0"},
    "Turkey": {"water_color": "#81D5E9", "land_color": "#F4EFE3"},
    "Ukraine": {"water_color": "#7FD5E9", "land_color": "#C6F0DA"},
    "UnitedArabEmirates": {"water_color": "#87D8EB", "land_color": "#F6F2E9"},
    "UnitedKingdom": {"water_color": "#7CD5E9", "land_color": "#C2EFDA"},
    "UnitedStates": {"water_color": "#78D4E9", "land_color": "#BCECD8"},
    "Uruguay": {"water_color": "#84D7EB", "land_color": "#CDF4DF"},
    "Vietnam": {"water_color": "#7ED5E9", "land_color": "#B2EBCF"},
}
BORDER_COLOR = "#000000"
GLACIER_COLOR = "#FFFFFF"
ROAD_COLOR = "#ACB7CD"
ROAD_STROKE_WIDTH = 1.5

NATIONAL_STROKE_WIDTH = 1
PROVINCE_STROKE_WIDTH = 1
PROVINCE_DASHARRAY = "2,3"

HEIGHT = 800
PADDING = 2
PRECISION = 5

MOSTLY_COVERS_THRESHOLD = 0.5
MINIMUM_AREA = 1e-10
OVERLAY_GAP_POINT_SPACING = 8  # px in the output SVG's coordinate space (HEIGHT=800)

NAME_FIELD_GUESSES = ["shapeName", "NAME_1", "NAME_2", "NAME_3", "NAME_4", "NAME", "name", "ref"]

# For point datasets (e.g. gazetteers) that carry the whole admin hierarchy
# as columns on every row (NAME_1..NAME_4 plus the point's own name), we
# want the point's own, most specific name -- so this is the reverse of
# NAME_FIELD_GUESSES: try the finest NAME_X first, and only fall back to
# the coarse/generic fields if no NAME_X is present at all.
POINT_NAME_FIELD_GUESSES = ["NAME_4", "NAME_3", "NAME_2", "NAME_1", "shapeName", "NAME", "name"]
