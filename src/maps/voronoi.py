"""Bounded centroidal Voronoi tessellation (CVT) of the --points/--extra
data, grouped by department and renamed/unioned to AreaCode.

Pipeline (see build_area_code_geometries, the single entry point):

  1. Points already carry two independent identities computed elsewhere:
     - their OWN raw (Province, Department, Locality) properties
       (NAME_1/NAME_2/finest-NAME_X), which is what --extra's CSV rows key
       against (see topo_io.filter_points_by_extra) and therefore what we
       use to look up each point's AreaCode;
     - the fine-level admin polygon they spatially fell inside
       (point_pairs, from geometry.match_points_to_polygons), which is
       what actually defines the "department" whose boundary bounds the
       Voronoi computation.
     These two normally agree, but nothing here assumes they must.

  2. Points are grouped by that spatial department (admin_idx into
     fine_feats). For each department with 2+ points, a bounded CVT is
     computed: repeated Voronoi-diagram + clip-to-boundary + recenter-on-
     centroid (Lloyd's algorithm) until seed movement is negligible or an
     iteration cap is hit. A department with exactly one point needs no
     computation at all: its one cell is simply the whole department.

  3. Every resulting cell is looked up by its owning point's AreaCode and
     accumulated; AreaCodes that are shared by several localities (in the
     same department or different ones) end up as the union of every cell
     that carries that code.

Everything here works in already-projected SVG pixel space (the same
project_point()/scale/off_x/off_y used by svg_render.py to draw the Land
and Points layers), rather than in raw lon/lat, so a department's Voronoi
boundary is pixel-for-pixel the same shape as its Land-layer path.
"""

import sys

import shapely
from geometry import extract_polygons, flatten_point_coords, to_multi_or_single_polygon
from shapely.geometry import MultiPoint, Point
from shapely.geometry import MultiPolygon as ShapelyMultiPolygon
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.ops import transform, unary_union, voronoi_diagram
from shapely.validation import make_valid
from svg_render import project_point
from topo_io import get_feature_name, group_depth, point_group_key

# Lloyd's-algorithm knobs. Implementation details, not exposed as CLI
# flags: 8 iterations comfortably converges typical department-sized point
# counts, and a 0.01px seed-movement tolerance is well below anything
# visible at this output's PRECISION/scale.
_CVT_ITERATIONS = 10
_CVT_TOLERANCE = 1e-5


def _point_csv_key(feat, n_levels):
    """A point's own identity, (NAME_1, ..., NAME_{N-1}, own name)."""
    return point_group_key(feat, n_levels)


def _project_ring(ring, lon0, cos_lat0, scale, off_x, off_y):
    pts = []
    for lon, lat in ring:
        x, y = project_point(lon, lat, lon0, cos_lat0)
        pts.append((x * scale + off_x, y * scale + off_y))
    return pts


def _project_department_boundary(geometry, lon0, cos_lat0, scale, off_x, off_y, label):
    """Project a department's own geometry (the same dict svg_render's
    render_fine_group() draws for the Land layer) into an SVG-pixel-space
    shapely polygon, repairing it with make_valid() in the rare case the
    raw rings are self-intersecting -- mirrors geometry.feature_to_shape()
    so this stays valid for the intersection/centroid ops CVT needs.
    """
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    polys = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []

    shapely_polys = []
    for poly in polys:
        rings = [_project_ring(r, lon0, cos_lat0, scale, off_x, off_y) for r in poly]
        rings = [r for r in rings if len(r) >= 3]
        if not rings:
            continue
        shapely_polys.append(ShapelyPolygon(rings[0], rings[1:]))

    if not shapely_polys:
        return ShapelyPolygon()

    boundary = shapely_polys[0] if len(shapely_polys) == 1 else ShapelyMultiPolygon(shapely_polys)

    if not boundary.is_valid:
        repaired_polys = extract_polygons(make_valid(boundary))
        if repaired_polys:
            boundary = to_multi_or_single_polygon(repaired_polys)
            print(
                f"  ⚠ Límite de departamento {label!r} inválido, reparado para Voronoi.",
                file=sys.stderr,
            )
        else:
            boundary = ShapelyPolygon()

    return boundary


def _voronoi_cells_by_index(seeds, boundary):
    """Voronoi-partition `boundary` using `seeds` as generators, returning
    one clipped cell per seed, index-aligned with `seeds`. Cell-to-seed
    correspondence has to be recovered after the fact because
    shapely.ops.voronoi_diagram() returns its cells in no particular
    order: each raw (unclipped) cell is guaranteed to cover exactly the
    one seed it was generated from, so that's used as the join key.
    """
    n = len(seeds)
    if n == 0:
        return []
    if n == 1:
        return [boundary]

    diagram = voronoi_diagram(MultiPoint(seeds), envelope=boundary)
    seed_points = [Point(s) for s in seeds]

    # Two cells that share a Voronoi edge are each clipped against
    # `boundary` in their OWN separate `.intersection()` call below. Where
    # that shared edge crosses the boundary, GEOS computes the new vertex
    # independently each time, and the two computations can drift by a few
    # ULPs from one another -- invisible as an area difference, but it
    # leaves a literal sliver polygon behind once cells are unioned later.
    # Snapping both operands onto a common grid before intersecting forces
    # both calls to round to the exact same coordinate there, closing it.
    CLIP_PRECISION_GRID = 1e-5
    gridded_boundary = shapely.set_precision(boundary, CLIP_PRECISION_GRID)

    cells = [None] * n
    for raw_cell in diagram.geoms:
        for idx, sp in enumerate(seed_points):
            if cells[idx] is None and raw_cell.covers(sp):
                gridded_cell = shapely.set_precision(raw_cell, CLIP_PRECISION_GRID)
                cells[idx] = gridded_cell.intersection(gridded_boundary)
                break

    for idx in range(n):
        if cells[idx] is None:
            # Defensive only -- shouldn't trigger for distinct seeds inside
            # the boundary, which is the only case this is ever called with.
            cells[idx] = Point(seeds[idx]).buffer(1e-6).intersection(gridded_boundary)

    return cells


def _bounded_centroidal_voronoi(seeds, boundary):
    """Lloyd's algorithm: repeatedly replace each seed with its own cell's
    centroid and re-partition, until cells stop moving (or the iteration
    cap is hit). Returns final cells, index-aligned with the ORIGINAL
    `seeds` order (seed identity, i.e. which locality owns which cell,
    never changes across iterations -- only its position does).
    """
    if len(seeds) <= 1:
        return _voronoi_cells_by_index(seeds, boundary)

    current = list(seeds)
    cells = _voronoi_cells_by_index(current, boundary)

    for _ in range(_CVT_ITERATIONS):
        next_seeds = []
        max_shift = 0.0
        for cell, seed in zip(cells, current, strict=False):
            if cell is None or cell.is_empty or cell.area <= 0:
                next_seeds.append(seed)
                continue
            c = cell.centroid
            max_shift = max(max_shift, ((c.x - seed[0]) ** 2 + (c.y - seed[1]) ** 2) ** 0.5)
            next_seeds.append((c.x, c.y))

        current = next_seeds
        cells = _voronoi_cells_by_index(current, boundary)
        if max_shift < _CVT_TOLERANCE:
            break

    return cells


def build_area_code_geometries(
    point_pairs, fine_feats, group_rows, lon0, cos_lat0, scale, off_x, off_y
):
    """Entry point: turn matched points into {GroupName: shapely geometry},
    every geometry already in SVG pixel space and ready for
    svg_render.render_area_codes_group().

    point_pairs: (point_feat, admin_idx) pairs from
        geometry.match_points_to_polygons(..., fine_geoms) -- admin_idx is
        None for a point that didn't spatially land in any polygon.
    fine_feats: the fine-level admin feature list matching that admin_idx.
    group_rows: the --group CSV rows, as loaded by topo_io.load_group_csv().
    """
    n_levels = group_depth(group_rows)
    area_code_by_key = {
        row["levels"]: row["GroupName"] for row in group_rows if len(row["levels"]) == n_levels
    }

    by_department = {}
    for feat, admin_idx in point_pairs:
        key = _point_csv_key(feat, n_levels)
        label = "/".join(str(k) for k in key)

        if admin_idx is None:
            print(
                f"  ⚠ Punto {label} no cayó espacialmente dentro de ningún polígono; se omite.",
                file=sys.stderr,
            )
            continue

        area_code = feat.get("properties", {}).get("_matched_group")

        if area_code is None:
            area_code = area_code_by_key.get(key)

        if area_code is None:
            print(f"  ⚠ Punto {label} no tiene grupo en --group; se omite.", file=sys.stderr)
            continue

        coords = list(flatten_point_coords(feat["geometry"]))
        if not coords:
            continue
        projected = [
            (px * scale + off_x, py * scale + off_y)
            for px, py in (project_point(lon, lat, lon0, cos_lat0) for lon, lat in coords)
        ]
        seed = (
            sum(p[0] for p in projected) / len(projected),
            sum(p[1] for p in projected) / len(projected),
        )

        by_department.setdefault(admin_idx, []).append((seed, area_code))

    geoms_by_area_code = {}
    for admin_idx, entries in by_department.items():
        label = get_feature_name(fine_feats[admin_idx]) or f"#{admin_idx}"
        boundary = _project_department_boundary(
            fine_feats[admin_idx]["geometry"], lon0, cos_lat0, scale, off_x, off_y, label
        )
        if boundary.is_empty:
            continue

        seeds = [e[0] for e in entries]
        area_codes = [e[1] for e in entries]

        unique_codes = set(area_codes)
        if len(unique_codes) == 1:
            # Every point in this polygon shares one group: nothing to
            # partition, so the whole polygon goes to that group.
            geoms_by_area_code.setdefault(area_codes[0], []).append(boundary)
            continue

        cells = _bounded_centroidal_voronoi(seeds, boundary)

        for cell, area_code in zip(cells, area_codes, strict=False):
            if cell is None or cell.is_empty:
                continue
            geoms_by_area_code.setdefault(area_code, []).append(cell)

    return {area_code: unary_union(geoms) for area_code, geoms in geoms_by_area_code.items()}


def group_polygons_by_area_code(feats, geoms, code_field):
    """Group already-valid shapely geometries (see geometry.build_valid_geoms)
    by their area-code property, unioning every polygon feature that
    shares a code -- a source dataset may split one area code into
    several disjoint parts or overlay polygons -- into a single
    (Multi)Polygon per code.
    """
    groups = {}
    for feat, geom in zip(feats, geoms, strict=False):
        code = feat.get("properties", {}).get(code_field)
        if code is None:
            continue
        groups.setdefault(str(code), []).append(geom)
    return {code: unary_union(g) for code, g in groups.items()}


def project_geometry(geom, lon0, cos_lat0, scale, off_x, off_y):
    """Project an already-computed lon/lat shapely geometry into SVG
    pixel space -- the same transform svg_render.project_point() applies
    to raw GeoJSON coordinates -- for geometries (like the polygon-Voronoi
    output above) that were computed entirely in lon/lat and only need to
    become pixel space once, at the very end, unlike
    build_area_code_geometries() which projects its seed points up front
    and works in pixel space throughout.
    """

    def _proj(lon, lat):
        x, y = project_point(lon, lat, lon0, cos_lat0)
        return x * scale + off_x, y * scale + off_y

    return transform(_proj, geom)
