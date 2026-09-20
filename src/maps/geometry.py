"""Geometric analysis of already-loaded features.

Everything here answers questions about shape: is this polygon valid, does
it mostly cover that one, what does the clipped intersection look like,
what's this ring's area, does this polygon touch the outer frame. Some of
it goes through shapely (validity repair, spatial indexing, coverage,
clipping) and some is plain coordinate math on GeoJSON-shaped dicts (area/
bounds, used before a shapely geometry is even built, e.g. for island
pruning). Both halves are kept together because they answer the same kind
of question about the data -- as opposed to topo_io.py, which is only
concerned with getting that data off disk, or svg_render.py, which turns
already-analyzed geometry into on-page paths.
"""

import math
import operator
import sys

from constants import MINIMUM_AREA, MOSTLY_COVERS_THRESHOLD
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree
from shapely.validation import make_valid
from topo_io import get_feature_name

# ---- plain coordinate math (no shapely) ----


def flatten_coords(geometry):
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    polygons = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []
    for poly in polygons:
        for ring in poly:
            yield from ring


def flatten_line_coords(geometry):
    """Yield every [lon, lat] coordinate pair from a LineString/
    MultiLineString/GeometryCollection-of-lines geometry dict.
    """
    gtype = geometry["type"]
    if gtype == "LineString":
        yield from geometry["coordinates"]
    elif gtype == "MultiLineString":
        for line in geometry["coordinates"]:
            yield from line
    elif gtype == "GeometryCollection":
        for g in geometry.get("geometries", []):
            yield from flatten_line_coords(g)


def flatten_point_coords(geometry):
    """Yield every [lon, lat] coordinate pair from a Point/MultiPoint/
    GeometryCollection-of-points geometry dict.
    """
    gtype = geometry["type"]
    if gtype == "Point":
        yield geometry["coordinates"]
    elif gtype == "MultiPoint":
        yield from geometry["coordinates"]
    elif gtype == "GeometryCollection":
        for g in geometry.get("geometries", []):
            yield from flatten_point_coords(g)


def point_geometry_bounds(geometry):
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf
    has_points = False
    for lon, lat in flatten_point_coords(geometry):
        has_points = True
        min_lon = min(min_lon, lon)
        max_lon = max(max_lon, lon)
        min_lat = min(min_lat, lat)
        max_lat = max(max_lat, lat)
    if not has_points:
        return None
    return (min_lon, min_lat, max_lon, max_lat)


def get_national_frame(features):
    coords = [pt for feat in features for pt in flatten_coords(feat["geometry"])]
    lons = [pt[0] for pt in coords]
    lats = [pt[1] for pt in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def geometry_bounds(geometry):
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf
    has_points = False
    for lon, lat in flatten_coords(geometry):
        has_points = True
        min_lon = min(min_lon, lon)
        max_lon = max(max_lon, lon)
        min_lat = min(min_lat, lat)
        max_lat = max(max_lat, lat)
    if not has_points:
        return None
    return (min_lon, min_lat, max_lon, max_lat)


def line_geometry_bounds(geometry):
    min_lon = min_lat = math.inf
    max_lon = max_lat = -math.inf
    has_points = False
    for lon, lat in flatten_line_coords(geometry):
        has_points = True
        min_lon = min(min_lon, lon)
        max_lon = max(max_lon, lon)
        min_lat = min(min_lat, lat)
        max_lat = max(max_lat, lat)
    if not has_points:
        return None
    return (min_lon, min_lat, max_lon, max_lat)


def bounds_overlap(bounds_a, bounds_b) -> bool:
    if bounds_a is None or bounds_b is None:
        return False
    a_min_lon, a_min_lat, a_max_lon, a_max_lat = bounds_a
    b_min_lon, b_min_lat, b_max_lon, b_max_lat = bounds_b
    return not (
        a_max_lon < b_min_lon
        or b_max_lon < a_min_lon
        or a_max_lat < b_min_lat
        or b_max_lat < a_min_lat
    )


def ring_area(ring):
    area = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def polygon_area(poly_coords):
    if not poly_coords:
        return 0.0
    area = ring_area(poly_coords[0])
    for hole in poly_coords[1:]:
        area -= ring_area(hole)
    return abs(area)


def geometry_total_area(geometry):
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    polys = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []
    return sum(polygon_area(p) for p in polys)


def polygon_bounds(poly):
    pts = [pt for ring in poly for pt in ring]
    lons = [pt[0] for pt in pts]
    lats = [pt[1] for pt in pts]
    return (min(lons), min(lats), max(lons), max(lats))


def polygon_touches_frame(poly, frame, epsilon=MINIMUM_AREA):
    min_lon, min_lat, max_lon, max_lat = frame
    p_min_lon, p_min_lat, p_max_lon, p_max_lat = polygon_bounds(poly)
    return (
        abs(p_min_lon - min_lon) <= epsilon
        or abs(p_min_lat - min_lat) <= epsilon
        or abs(p_max_lon - max_lon) <= epsilon
        or abs(p_max_lat - max_lat) <= epsilon
    )


def prune_small_edge_parts(fine_feats, coarse_feats, min_area):
    changed = True
    total_removed = 0
    while changed:
        changed = False
        frame = get_national_frame(fine_feats)
        for feat in fine_feats + coarse_feats:
            geometry = feat["geometry"]
            if geometry["type"] != "MultiPolygon":
                continue

            parts = []
            for poly in geometry["coordinates"]:
                area = polygon_area(poly)
                touches_frame = polygon_touches_frame(poly, frame)
                parts.append((area, poly, touches_frame))

            kept = []
            for area, poly, touches_frame in parts:
                should_remove = area < min_area and touches_frame
                if should_remove:
                    total_removed += 1
                    changed = True
                else:
                    kept.append(poly)

            if not kept:
                largest = max(parts, key=operator.itemgetter(0))
                kept = [largest[1]]

            if len(kept) == 1:
                feat["geometry"] = {"type": "Polygon", "coordinates": kept[0]}
            else:
                feat["geometry"] = {"type": "MultiPolygon", "coordinates": kept}

    return total_removed


# ---- shapely-based analysis ----


def extract_polygons(geom):
    if geom is None or geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 0 else []
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty and p.area > 0]
    if isinstance(geom, GeometryCollection):
        polys = []
        for g in geom.geoms:
            polys.extend(extract_polygons(g))
        return polys
    return []


def to_multi_or_single_polygon(polygons):
    valid_polys = []
    for p in polygons:
        valid_polys.extend(extract_polygons(p))
    if not valid_polys:
        return Polygon()
    if len(valid_polys) == 1:
        return valid_polys[0]
    return MultiPolygon(valid_polys)


def clean_to_geojson_dict(geom):
    poly = to_multi_or_single_polygon(extract_polygons(geom))
    return mapping(poly)


def feature_to_shape(feat, source_label=""):
    """Convert a GeoJSON-shaped feature's geometry to a shapely geometry,
    repairing it with make_valid() if GEOS considers it invalid (e.g. a
    self-intersecting ring). Repaired geometries are collapsed back down
    to a plain Polygon/MultiPolygon, since make_valid() can return a
    GeometryCollection (e.g. if a self-intersection splits off a stray
    line or point) that downstream union/intersection code doesn't expect.
    Prints a one-line notice per repair so silent data problems stay
    visible.

    If the geometry cannot be repaired into any usable polygon (e.g. it
    collapses to a zero-area sliver once GEOS untangles the
    self-intersection), the feature is unusable: this returns None
    instead of raising. Callers are responsible for dropping any feature
    for which this returns None (see build_valid_geoms).
    """
    geom = shape(feat["geometry"])
    if geom.is_valid:
        return geom

    name = get_feature_name(feat) or "?"
    repaired = make_valid(geom)
    polys = extract_polygons(repaired)
    if not polys:
        print(
            f"  ⚠ Geometría inválida en {source_label} {name!r} no se pudo reparar en "
            f"ningún polígono utilizable (entidad descartada).",
            file=sys.stderr,
        )
        return None
    fixed = to_multi_or_single_polygon(polys)
    print(
        f"  ⚠ Geometría inválida reparada en {source_label} {name!r} "
        f"(auto-intersección u otro defecto corregido con make_valid).",
        file=sys.stderr,
    )
    return fixed


def build_valid_geoms(feats, label):
    """Compute a shapely geometry for every feature via feature_to_shape(),
    dropping (with a one-line warning) any feature whose geometry can't be
    repaired into a usable polygon instead of crashing the whole run.

    Returns (kept_feats, kept_geoms, kept_indices), where kept_indices are
    the original 0-based positions in `feats` that survived. Most callers
    only need kept_feats/kept_geoms; kept_indices exists for callers that
    must keep a second, independently loaded parallel list in sync (e.g.
    the raw TopoJSON arc features used for province borders).
    """
    kept_feats, kept_geoms, kept_indices = [], [], []
    for i, f in enumerate(feats):
        geom = feature_to_shape(f, label)
        if geom is None:
            continue
        kept_feats.append(f)
        kept_geoms.append(geom)
        kept_indices.append(i)

    dropped = len(feats) - len(kept_feats)
    if dropped:
        print(
            f"  ⚠ {dropped} entidad(es) de {label} descartada(s) por geometría irreparable.",
            file=sys.stderr,
        )

    return kept_feats, kept_geoms, kept_indices


def mostly_covers(coarse_geom, fine_geom, threshold=MOSTLY_COVERS_THRESHOLD):
    """Return True if `coarse_geom` mostly covers `fine_geom`, i.e. the
    fraction of fine_geom's own area that lies inside coarse_geom is >=
    threshold. Boolean only — the threshold is fixed at call time.
    """
    if coarse_geom is None or fine_geom is None or coarse_geom.is_empty or fine_geom.is_empty:
        return False
    fine_area = fine_geom.area
    if fine_area <= MINIMUM_AREA:
        return False
    inter_area = coarse_geom.intersection(fine_geom).area
    return (inter_area / fine_area) >= threshold


def build_spatial_index(geoms):
    """STRtree over non-empty geoms. Returns (tree, pos_to_idx) or None."""
    non_empty = [i for i in range(len(geoms)) if not geoms[i].is_empty]
    if not non_empty:
        return None
    tree = STRtree([geoms[i] for i in non_empty])
    pos_to_idx = dict(enumerate(non_empty))
    return tree, pos_to_idx


def nearby_candidates(index, geom, margin_factor=0.0):
    if index is None or geom.is_empty:
        return []
    tree, pos_to_idx = index
    positions = tree.query(geom)
    return [pos_to_idx[int(p)] for p in positions]


def group_fine_by_coarse(
    fine_feats, fine_geoms, coarse_feats, coarse_geoms, threshold=MOSTLY_COVERS_THRESHOLD
):
    """Assign each fine-level polygon to exactly one coarse-level polygon.

    For each fine polygon, every coarse candidate is scored by coverage
    fraction (area(candidate ∩ fine) / area(fine)):
      - 2+ candidates >= threshold: real data conflict (overlapping coarse
        polygons) -- fails immediately, stopping the whole run right there.
      - exactly 1 candidate >= threshold: assigned to it.
      - 0 candidates >= threshold: falls back to whichever candidate has
        the highest positive overlap (best-effort match), with a warning.
      - 0 candidates with ANY overlap at all: fails immediately, since
        there's nothing to assign to.
    """
    coarse_index = build_spatial_index(coarse_geoms)
    assignment = [-1] * len(fine_feats)

    for i, fgeom in enumerate(fine_geoms):
        name = get_feature_name(fine_feats[i]) or f"#{i}"
        if fgeom.is_empty:
            sys.exit(f"Error: entidad fina {name!r} tiene geometría vacía, no se puede asignar.")

        candidate_idxs = nearby_candidates(coarse_index, fgeom)
        scored = []
        for j in candidate_idxs:
            if coarse_geoms[j].is_empty:
                continue
            frac = coarse_geoms[j].intersection(fgeom).area / fgeom.area if fgeom.area > 0 else 0.0
            scored.append((frac, j))

        matches = [(frac, j) for frac, j in scored if frac >= threshold]

        if len(matches) >= 2:
            names = ", ".join(
                f"{get_feature_name(coarse_feats[j]) or f'#{j}'} ({frac * 100:.1f}%)"
                for frac, j in matches
            )
            sys.exit(
                f"Error: entidad fina {name!r} está cubierta >= {threshold * 100:.0f}% por MÁS de una "
                f"entidad de nivel superior ({names})"
            )

        if len(matches) == 1:
            assignment[i] = matches[0][1]
            continue

        positive = [(frac, j) for frac, j in scored if frac > 0]
        if not positive:
            print(f"  ⚠ Entidad fina {name!r} no se solapa con ninguna entidad de nivel superior.")
            continue

        positive.sort(reverse=True)
        best_frac, best_j = positive[0]
        assignment[i] = best_j
        print(
            f"  ⚠ {name!r}: ninguna entidad de nivel superior cubre >= {threshold * 100:.0f}%; "
            f"asignada a la de mayor solapamiento: {get_feature_name(coarse_feats[best_j]) or f'#{best_j}'} "
            f"({best_frac * 100:.1f}%).",
            file=sys.stderr,
        )

    return assignment


def reconstruct_coarse_features(coarse_feats, assignment, fine_feats, fine_geoms):
    groups = {}
    for fine_idx, coarse_idx in enumerate(assignment):
        groups.setdefault(coarse_idx, []).append(fine_idx)

    rebuilt = []
    for coarse_idx, fine_indices in sorted(groups.items()):
        coarse_feat = coarse_feats[coarse_idx]
        merged = unary_union([fine_geoms[i] for i in fine_indices])
        new_feat = {
            "type": "Feature",
            "properties": dict(coarse_feat.get("properties", {})),
            "geometry": clean_to_geojson_dict(merged),
        }
        rebuilt.append(new_feat)

    return rebuilt


def clip_features_to_national(feats, geoms, national_geom):
    """Clip each polygon in `feats`/`geoms` to the national union, keeping
    only the portion that falls inside the country's border. Generic over
    what these polygons represent -- used for both the ecoregions overlay
    and the relief overlay, since the clipping logic is identical.

    Uses a spatial index over `geoms` to skip candidates whose bounding box
    can't possibly overlap the national border before paying for a real
    intersection. Returns a list of (feature, clipped_geom) pairs for every
    polygon whose intersection with the national border is non-empty
    (clipped_geom is that intersection, already restricted to the country
    -- never the original, unclipped shape).
    """
    index = build_spatial_index(geoms)
    candidate_idxs = nearby_candidates(index, national_geom)

    results = []
    for i in candidate_idxs:
        geom = geoms[i]
        if geom.is_empty:
            continue
        clipped = national_geom.intersection(geom)
        if clipped.is_empty or clipped.area <= MINIMUM_AREA:
            continue
        results.append((feats[i], clipped))
    return results


def match_points_to_polygons(point_feats, point_geoms, polygon_geoms):
    """Match each point feature to the single polygon (by index into
    `polygon_geoms`) that contains it, using a spatial index the same way
    clip_features_to_national() does. Unlike the clipping helpers, a point
    isn't cut down to an intersection -- it either falls inside exactly
    one polygon or it doesn't, since the polygons here (the fine-level
    admin polygons) are assumed non-overlapping.

    Returns a list of (point_feat, polygon_index) pairs, one per input
    point feature, in the same order as point_feats/point_geoms.
    polygon_index is None when the point doesn't fall inside any polygon
    (e.g. it's just offshore or outside the national border).
    """
    index = build_spatial_index(polygon_geoms)

    results = []
    for feat, pgeom in zip(point_feats, point_geoms, strict=False):
        if pgeom.is_empty:
            results.append((feat, None))
            continue

        match_idx = None
        for j in nearby_candidates(index, pgeom):
            if polygon_geoms[j].intersects(pgeom):
                match_idx = j
                break
        results.append((feat, match_idx))

    return results


def clip_lines_to_national(feats, geoms, national_geom):
    """Clip each line in `feats`/`geoms` to the national union, keeping
    only the portion that falls inside the country's border. Mirrors
    clip_features_to_national(), but measures by length instead of area
    since these are lines, not polygons.
    """
    index = build_spatial_index(geoms)
    candidate_idxs = nearby_candidates(index, national_geom)

    results = []
    for i in candidate_idxs:
        geom = geoms[i]
        if geom.is_empty:
            continue
        clipped = national_geom.intersection(geom)
        if clipped.is_empty or clipped.length <= MINIMUM_AREA:
            continue
        results.append((feats[i], clipped))
    return results
