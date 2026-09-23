"""Turning analyzed geometry into SVG.

Everything here is about presentation: projecting lon/lat onto the page,
turning rings/lines into "d" path strings, picking the CSS for each layer,
and assembling each named <g> layer (Land, Provinces, Country, Ecoregions,
Glaciers, Lakes, Roads). It takes already-loaded features (topo_io.py) and
already-analyzed/clipped geometry (geometry.py) as plain arguments and
produces strings -- it doesn't parse files or do spatial analysis itself.
"""

from constants import (
    BACKGROUND_COLOR,
    BORDER_COLOR,
    GLACIER_COLOR,
    LAND_COLOR,
    NATIONAL_STROKE_WIDTH,
    PRECISION,
    PROVINCE_DASHARRAY,
    PROVINCE_STROKE_WIDTH,
    ROAD_COLOR,
    ROAD_STROKE_WIDTH,
)
from geometry import clean_to_geojson_dict, flatten_point_coords
from shapely.geometry import mapping
from topo_io import get_feature_name, hierarchical_name_id, point_full_chain, slugify
from unidecode import unidecode


def project_point(lon, lat, lon0, cos_lat0):
    return (lon - lon0) * cos_lat0, -lat


def ring_to_path_d(ring, lon0, cos_lat0, scale, off_x, off_y):
    pts = []
    for lon, lat in ring:
        x, y = project_point(lon, lat, lon0, cos_lat0)
        pts.append(f"{x * scale + off_x:.{PRECISION}f},{y * scale + off_y:.{PRECISION}f}")
    return "M" + " ".join(pts) + "Z"


def geometry_to_path_d(geometry, lon0, cos_lat0, scale, off_x, off_y):
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    polygons = [coords] if gtype == "Polygon" else coords
    return " ".join(
        ring_to_path_d(ring, lon0, cos_lat0, scale, off_x, off_y)
        for poly in polygons
        for ring in poly
    )


def line_to_path_d(coords, lon0, cos_lat0, scale, off_x, off_y):
    pts = []
    for lon, lat in coords:
        x, y = project_point(lon, lat, lon0, cos_lat0)
        pts.append(f"{x * scale + off_x:.{PRECISION}f},{y * scale + off_y:.{PRECISION}f}")
    return "M" + " L".join(pts)


def line_geometry_to_path_d(geometry, lon0, cos_lat0, scale, off_x, off_y):
    """Like geometry_to_path_d(), but for open LineString/MultiLineString
    geometries: no closing 'Z', and sub-geometries inside a
    GeometryCollection (e.g. stray Points left over from clipping a road
    against the national border) that aren't lines are skipped.
    """
    gtype = geometry["type"]
    if gtype == "LineString":
        return line_to_path_d(geometry["coordinates"], lon0, cos_lat0, scale, off_x, off_y)
    if gtype == "MultiLineString":
        return " ".join(
            line_to_path_d(line, lon0, cos_lat0, scale, off_x, off_y)
            for line in geometry["coordinates"]
        )
    if gtype == "GeometryCollection":
        return " ".join(
            line_geometry_to_path_d(g, lon0, cos_lat0, scale, off_x, off_y)
            for g in geometry.get("geometries", [])
            if g.get("type") in {"LineString", "MultiLineString"}
        )
    return ""


def province_edge_paths(pair_arcs, decoded_arcs, lon0, cos_lat0, scale, off_x, off_y):
    results = []
    for pair, arc_indices in pair_arcs.items():
        name_a, name_b = sorted(pair, key=str.lower)
        subpaths = []
        for abs_idx in arc_indices:
            coords = decoded_arcs[abs_idx]
            pts = []
            for lon, lat in coords:
                x, y = project_point(lon, lat, lon0, cos_lat0)
                pts.append(f"{x * scale + off_x:.{PRECISION}f},{y * scale + off_y:.{PRECISION}f}")
            subpaths.append("M" + " L".join(pts))
        results.append((name_a, name_b, " ".join(subpaths)))

    results.sort(key=lambda e: (unidecode(e[0]).lower(), unidecode(e[1]).lower()))
    return results


def style_attrs(level) -> str:
    if level == "national":
        return f'style="opacity: 0.75;stroke: {BORDER_COLOR};stroke-width: {NATIONAL_STROKE_WIDTH};fill: none;pointer-events: none;"'
    if level == "province":
        return f'style="opacity: 0.25;stroke: {BORDER_COLOR};stroke-width: {PROVINCE_STROKE_WIDTH};stroke-dasharray: {PROVINCE_DASHARRAY};fill: none;pointer-events: none;"'
    if level == "glaciers":
        return f'style="fill: {GLACIER_COLOR};pointer-events: none;"'
    if level == "lakes":
        return f'style="fill: {BACKGROUND_COLOR};pointer-events: none;"'
    if level == "roads":
        return f'style="stroke: {ROAD_COLOR};stroke-width: {ROAD_STROKE_WIDTH};fill: none;pointer-events: none;"'
    if level == "points":
        return 'style="fill: none;stroke: none;pointer-events: none;"'
    if level == "areacodes":
        return 'style="fill: transparent;stroke: none;pointer-events: all;"'
    return f'style="stroke: {LAND_COLOR};stroke-width: 1;fill: {LAND_COLOR};"'


def render_national(geom, country_name, lon0, cos_lat0, scale, off_x, off_y) -> str:
    d = geometry_to_path_d(clean_to_geojson_dict(geom), lon0, cos_lat0, scale, off_x, off_y)
    vector_style = ' style="vector-effect: non-scaling-stroke;"'
    path = f'<path id="{country_name}.Border"{vector_style} d="{d}"/>'
    return f'<g id="Country" {style_attrs("national")}>{path}</g>'


def render_fine_group(feats, name_chains, group_id, lon0, cos_lat0, scale, off_x, off_y):
    if not feats:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'

    order = sorted(
        range(len(feats)),
        key=lambda i: tuple(unidecode(n or "").lower() for n in name_chains[i]),
    )

    paths = []
    for i in order:
        feat = feats[i]
        d = geometry_to_path_d(feat["geometry"], lon0, cos_lat0, scale, off_x, off_y)
        fid = hierarchical_name_id(name_chains[i], str(i))
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs("land")}>' + "".join(paths) + "</g>"


def render_province_group(pair_arcs, decoded_arcs, group_id, lon0, cos_lat0, scale, off_x, off_y):
    if not pair_arcs:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'
    edges = province_edge_paths(pair_arcs, decoded_arcs, lon0, cos_lat0, scale, off_x, off_y)

    paths = []
    for name_a, name_b, d in edges:
        fid = f"{slugify(name_a)}.{slugify(name_b)}.Border"
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs("province")}>' + "".join(paths) + "</g>"


def render_ecoregions_group(clipped_pairs, group_id, lon0, cos_lat0, scale, off_x, off_y):
    if not clipped_pairs:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'

    order = sorted(
        range(len(clipped_pairs)),
        key=lambda i: unidecode(get_feature_name(clipped_pairs[i][0]) or "").lower(),
    )

    paths = []
    for rank, i in enumerate(order):
        feat, geom = clipped_pairs[i]
        d = geometry_to_path_d(clean_to_geojson_dict(geom), lon0, cos_lat0, scale, off_x, off_y)
        name = get_feature_name(feat)
        fid = slugify(name) if name else f"Ecoregion_{rank}"
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs("land")}>' + "".join(paths) + "</g>"


def render_glaciers_group(clipped_pairs, group_id, lon0, cos_lat0, scale, off_x, off_y):
    if not clipped_pairs:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'

    order = sorted(
        range(len(clipped_pairs)),
        key=lambda i: unidecode(get_feature_name(clipped_pairs[i][0]) or "").lower(),
    )

    paths = []
    for rank, i in enumerate(order):
        feat, geom = clipped_pairs[i]
        d = geometry_to_path_d(clean_to_geojson_dict(geom), lon0, cos_lat0, scale, off_x, off_y)
        name = get_feature_name(feat)
        fid = slugify(name) if name else f"Glacier_{rank}"
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs("glaciers")}>' + "".join(paths) + "</g>"


def render_lakes_group(clipped_pairs, group_id, lon0, cos_lat0, scale, off_x, off_y):
    if not clipped_pairs:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'

    order = sorted(
        range(len(clipped_pairs)),
        key=lambda i: unidecode(get_feature_name(clipped_pairs[i][0]) or "").lower(),
    )

    paths = []
    for rank, i in enumerate(order):
        feat, geom = clipped_pairs[i]
        d = geometry_to_path_d(clean_to_geojson_dict(geom), lon0, cos_lat0, scale, off_x, off_y)
        name = get_feature_name(feat)
        fid = slugify(name) if name else f"Lake_{rank}"
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs("lakes")}>' + "".join(paths) + "</g>"


def render_roads_group(clipped_pairs, group_id, lon0, cos_lat0, scale, off_x, off_y):
    if not clipped_pairs:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'

    order = sorted(
        range(len(clipped_pairs)),
        key=lambda i: unidecode(get_feature_name(clipped_pairs[i][0]) or "").lower(),
    )

    paths = []
    for rank, i in enumerate(order):
        feat, geom = clipped_pairs[i]
        d = line_geometry_to_path_d(mapping(geom), lon0, cos_lat0, scale, off_x, off_y)
        if not d:
            continue
        name = get_feature_name(feat)
        fid = slugify(name) if name else f"Road_{rank}"
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs("roads")}>' + "".join(paths) + "</g>"


def render_points_group(
    point_pairs, admin_name_chains, group_id, lon0, cos_lat0, scale, off_x, off_y
):
    """Render a layer of invisible markers, one per point feature matched
    to an admin polygon (see geometry.match_points_to_polygons). Each
    marker's id is the admin polygon's own name chain (the same chain
    used for the Land layer's ids) with the point's own name appended --
    e.g. ADM_1.ADM_2.ADM_3.POINT_NAME -- so points inherit the Land
    naming scheme rather than getting their own. Points with no matching
    admin polygon are skipped.
    """
    usable = [(feat, idx) for feat, idx in point_pairs if idx is not None]
    if not usable:
        return ""

    order = sorted(
        range(len(usable)),
        key=lambda i: tuple(
            unidecode(n or "").lower()
            for n in point_full_chain(usable[i][0], admin_name_chains[usable[i][1]])
        ),
    )

    markers = []
    for rank, i in enumerate(order):
        feat, admin_idx = usable[i]
        chain = point_full_chain(feat, admin_name_chains[admin_idx])
        fid = hierarchical_name_id(chain, f"Point_{rank}")
        for lon, lat in flatten_point_coords(feat["geometry"]):
            x, y = project_point(lon, lat, lon0, cos_lat0)
            cx, cy = x * scale + off_x, y * scale + off_y
            markers.append(
                f'<circle id="{fid}" cx="{cx:.{PRECISION}f}" cy="{cy:.{PRECISION}f}" r="0"/>'
            )
    return f'<g id="{group_id}" {style_attrs("points")}>' + "".join(markers) + "</g>"


def shapely_geom_to_path_d(geom):
    """Format an already-projected (SVG pixel space) shapely geometry as
    an SVG path 'd' string. Unlike geometry_to_path_d(), this does NOT
    project lon/lat -- it's for layers computed directly in pixel space,
    like AreaCodes (see voronoi.build_area_code_geometries), where the
    geometry is the end result already, not raw geographic input.
    """
    geo_dict = clean_to_geojson_dict(geom)
    gtype = geo_dict["type"]
    coords = geo_dict["coordinates"]
    polygons = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []
    parts = []
    for poly in polygons:
        for ring in poly:
            pts = [f"{x:.{PRECISION}f},{y:.{PRECISION}f}" for x, y in ring]
            parts.append("M" + " ".join(pts) + "Z")
    return " ".join(parts)


def render_area_codes_group(area_code_geoms, group_id, style_level="areacodes"):
    """Render one <path> per group -- the union of every geometry that
    shares that group name (see voronoi.build_area_code_geometries and
    grouping.build_land_group_geometries). Geometries arrive already
    projected into SVG pixel space, so no lon0/cos_lat0/scale/off_x/off_y
    are needed here, unlike every other render_*_group() in this file.
    style_level picks the CSS: "areacodes" (transparent overlay) or
    "land" (filled like the normal Land layer).
    """
    if not area_code_geoms:
        return ""
    vector_style = ' style="vector-effect: non-scaling-stroke;"'

    paths = []
    for area_code in sorted(area_code_geoms, key=str):
        d = shapely_geom_to_path_d(area_code_geoms[area_code])
        if not d:
            continue
        fid = slugify(str(area_code))
        paths.append(f'<path id="{fid}"{vector_style} d="{d}"/>')
    return f'<g id="{group_id}" {style_attrs(style_level)}>' + "".join(paths) + "</g>"
