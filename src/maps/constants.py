"""Shared constants for country_to_svg_v2.

Kept in one place because the same values (colors, tolerances, the output
canvas size) are each used from more than one of the other modules, and
duplicating them would risk the copies drifting apart.
"""

# ---- fixed style constants ----
BACKGROUND_COLOR = "#7CD5E9"
LAND_COLOR = "#B1EBCF"
BORDER_COLOR = "#000000"
ERROR_COLOR = "#FF0000"  # Unresolved / unmatched slivers
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

NAME_FIELD_GUESSES = ["shapeName", "NAME_1", "NAME_2", "NAME_3", "NAME_4", "NAME", "name", "ref"]

# For point datasets (e.g. gazetteers) that carry the whole admin hierarchy
# as columns on every row (NAME_1..NAME_4 plus the point's own name), we
# want the point's own, most specific name -- so this is the reverse of
# NAME_FIELD_GUESSES: try the finest NAME_X first, and only fall back to
# the coarse/generic fields if no NAME_X is present at all.
POINT_NAME_FIELD_GUESSES = ["NAME_4", "NAME_3", "NAME_2", "NAME_1", "shapeName", "NAME", "name"]
