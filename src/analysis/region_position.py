"""region_position.py — Where inside an area (bounding box) a point is: central / northern / ...

The box is cut into 5 portions of equal area: a central rectangle (20% of the box) and four
trapezoids around it (split by the diagonals). Cheap enough to call for every map sample.
"""

from collections import Counter

CENTRAL_SHARE = 0.2
_HALF = CENTRAL_SHARE**0.5  # half-size of the central rectangle, as a fraction of the box half-size


def position_in_box(lat: float, lng: float, bounds) -> str:
    """Bounds = (west, south, east, north). Returns central/northern/southern/eastern/western."""
    west, south, east, north = bounds
    hw, hh = (east - west) / 2, (north - south) / 2
    if hw <= 0 or hh <= 0:
        return "central"
    u = (lng - (west + east) / 2) / hw
    v = (lat - (south + north) / 2) / hh
    if abs(u) <= _HALF and abs(v) <= _HALF:
        return "central"
    if abs(u) >= abs(v):
        return "eastern" if u > 0 else "western"
    return "northern" if v > 0 else "southern"


def parts_text(points, bounds, min_share: float = 0.25, max_parts: int = 3) -> str:
    """Points = [(lat, lng)]. 'eastern and central': the portions that hold most of the points."""
    if not points or not bounds:
        return ""
    counts = Counter(position_in_box(lat, lng, bounds) for lat, lng in points)
    total = sum(counts.values())
    names = [n for n, c in counts.most_common() if c / total >= min_share][:max_parts]
    names = names or [counts.most_common(1)[0][0]]
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]
