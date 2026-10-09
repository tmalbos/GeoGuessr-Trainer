"""Bounded Voronoi tessellation of the --points/--extra data, grouped by
department and renamed/unioned to AreaCode.

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

  2. Every fine-level polygon ends up in the result, mirroring land mode:
     - a polygon claimed whole by a shallow CSV row (land_owner) goes
       entirely to that row's group;
     - otherwise, a polygon with matched points is split: with 2+ points
       of different groups a bounded Voronoi diagram is computed with the
       seeds FIXED at the points' own positions (every point claims the
       area closer to it than to any other point, clipped to the
       department). A cell whose clipped shape comes out in several
       disconnected pieces (a bay or channel let it reach across) keeps
       only the piece its seed is in; the other pieces are handed to the
       neighbouring cell they share the longest border with. If all the
       polygon's points share one group, the whole polygon goes to it;
     - otherwise (no owner, no points) the polygon passes through under
       its own Land id, exactly like land mode.

  3. Every resulting cell is accumulated by group; groups that are shared
     by several localities (in the same department or different ones) end
     up as the union of every cell that carries that group. Unions are
     done on a fixed snap grid so that the ~1e-14 floating-point seams
     between independently clipped cells can't survive as hairline slits.

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
from shapely.ops import transform, voronoi_diagram
from shapely.strtree import STRtree
from shapely.validation import make_valid
from svg_render import project_point
from topo_io import get_feature_name, group_depth, hierarchical_name_id, point_group_key

# Lloyd relaxation passes. 0 = seeds stay exactly where the points are
# (pure "everyone claims what is closest to them", so a point boxed in by
# its neighbours gets a small cell). Raising it re-centres seeds on their
# own cells and evens cell sizes out, which is the opposite behaviour.
_LLOYD_ITERATIONS = 0
_LLOYD_TOLERANCE = 1e-5

# Snap grid for unions, in output-SVG pixels. Finer than the SVG's own
# output precision (constants.PRECISION = 5 decimals) so it never moves
# anything visibly, but far coarser than the ~1e-14 float noise that
# creates seams.
_UNION_GRID = 1e-6


# Holes smaller than this (px^2) inside a unioned shape are seams left by
# independently clipped cells, not real gaps: a real hole is a whole
# unclaimed enclave and is orders of magnitude bigger.
_MIN_HOLE_AREA = 1e-3


def _drop_tiny_holes(geom):
    polys = extract_polygons(geom)
    cleaned = [
        ShapelyPolygon(
            p.exterior, [r for r in p.interiors if ShapelyPolygon(r).area >= _MIN_HOLE_AREA]
        )
        for p in polys
    ]
    return to_multi_or_single_polygon(cleaned)


# Hairline cracks narrower than 2 * this (px) between cells that should be
# one shape are closed. Independently clipped cells that share a long
# straight edge can end up with that edge a hair apart (one endpoint
# snapped differently), leaving a wedge-shaped crack that is open to the
# outside, so the small-hole filter above can't see it.
_SEAM_CLOSE = 1e-3


def union_geoms(geoms):
    """Union on the snap grid (see _UNION_GRID), with hairline cracks
    closed (see _SEAM_CLOSE) and seam-sized holes removed (see
    _MIN_HOLE_AREA).
    """
    merged = shapely.union_all(list(geoms), grid_size=_UNION_GRID)
    closed = merged.buffer(_SEAM_CLOSE, join_style="mitre").buffer(-_SEAM_CLOSE, join_style="mitre")
    return _drop_tiny_holes(closed if not closed.is_empty else merged)


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
    so this stays valid for the intersection ops the expansion needs.
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
    """Euclidean Voronoi-partition `boundary` using `seeds` as generators,
    returning one clipped cell per seed, index-aligned with `seeds`.
    Cell-to-seed correspondence has to be recovered after the fact because
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

    cells = [None] * n
    for raw_cell in diagram.geoms:
        for idx, sp in enumerate(seed_points):
            if cells[idx] is None and raw_cell.covers(sp):
                cells[idx] = raw_cell.intersection(boundary)
                break

    return cells


def voronoi_cells_in_boundary(seeds, boundary):
    """Public entry point: one connected cell per seed, index-aligned,
    partitioning `boundary` (see _bounded_voronoi). Used by
    build_area_code_geometries below AND by overlay_fill.fill_overlay_gaps,
    so the Points/--group path and the --overlay path share one algorithm.
    """
    return _bounded_voronoi(seeds, boundary)


def _lloyd(seeds, boundary, iterations=_LLOYD_ITERATIONS):
    """Voronoi cells for `seeds` inside `boundary`, optionally relaxed with
    Lloyd's algorithm. Returns (cells, final_seeds), both index-aligned
    with the ORIGINAL `seeds` order. With iterations == 0 this is a single
    plain pass and final_seeds == seeds.
    """
    current = list(seeds)
    cells = _voronoi_cells_by_index(current, boundary)
    for _ in range(iterations):
        nxt, max_shift = [], 0.0
        for cell, seed in zip(cells, current, strict=False):
            if cell is None or cell.is_empty or cell.area <= 0:
                nxt.append(seed)
                continue
            c = cell.centroid
            max_shift = max(max_shift, ((c.x - seed[0]) ** 2 + (c.y - seed[1]) ** 2) ** 0.5)
            nxt.append((c.x, c.y))
        current = nxt
        cells = _voronoi_cells_by_index(current, boundary)
        if max_shift < _LLOYD_TOLERANCE:
            break
    return cells, current


def _bounded_voronoi(seeds, boundary):
    """One cell per seed (index-aligned), each connected, partitioning
    `boundary` between them.

    Clipping a Euclidean Voronoi cell to a non-convex boundary can leave
    pieces of it cut off from its seed (it reached across a bay or an
    inlet). Those would not be reachable by a "liquid" poured at the seed
    without crossing a wall, so each such piece is moved to the neighbour
    it shares the longest border with.
    """
    n = len(seeds)
    if n <= 1:
        return _voronoi_cells_by_index(seeds, boundary)

    cells, final_seeds = _lloyd(seeds, boundary)

    mains = [None] * n
    orphans = []  # (owner index, polygon)
    for i, (cell, seed) in enumerate(zip(cells, final_seeds, strict=False)):
        if cell is None or cell.is_empty:
            continue
        parts = extract_polygons(cell)
        if not parts:
            continue
        sp = Point(seed)
        parts.sort(key=lambda p: (p.distance(sp), -p.area))
        mains[i] = parts[0]
        orphans.extend((i, p) for p in parts[1:])

    live = [j for j, m in enumerate(mains) if m is not None]
    tree = STRtree([mains[j] for j in live]) if live else None

    extra = [[] for _ in range(n)]
    for owner, piece in orphans:
        best, best_len = None, 0.0
        if tree is not None:
            for pos in tree.query(piece, predicate="intersects"):
                j = live[int(pos)]
                if j == owner:
                    continue
                shared = piece.boundary.intersection(mains[j].boundary).length
                if shared > best_len:
                    best, best_len = j, shared
        if best is None:
            # Touches nobody along an edge: give it to the closest cell.
            candidates = [(mains[j].distance(piece), j) for j in live if j != owner]
            if candidates:
                best = min(candidates)[1]
        if best is not None:
            extra[best].append(piece)

    out = []
    for main, add in zip(mains, extra, strict=False):
        if main is None:
            out.append(None if not add else union_geoms(add))
        else:
            out.append(union_geoms([main, *add]) if add else main)
    return out


def build_area_code_geometries(
    point_pairs,
    fine_feats,
    group_rows,
    name_chains,
    land_owner,
    lon0,
    cos_lat0,
    scale,
    off_x,
    off_y,
):
    """Entry point: turn matched points into {GroupName: shapely geometry},
    every geometry already in SVG pixel space and ready for
    svg_render.render_area_codes_group().

    point_pairs: (point_feat, admin_idx) pairs from
        geometry.match_points_to_polygons(..., fine_geoms) -- admin_idx is
        None for a point that didn't spatially land in any polygon.
    fine_feats: the fine-level admin feature list matching that admin_idx.
    group_rows: the --group CSV rows, as loaded by topo_io.load_group_csv().
    name_chains: per-fine-polygon name chains (same order as fine_feats),
        used to give pass-through polygons their normal Land id.
    land_owner: {admin_idx: GroupName} for polygons claimed whole by a
        shallow CSV row. Every fine polygon ends up in the result: owned
        -> its group; has matched points -> Voronoi split; otherwise ->
        its own Land id, exactly like land mode.
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

        owner_code = land_owner.get(admin_idx)
        if owner_code is not None:
            if owner_code != area_code:
                sys.exit(
                    f"Error: el punto {label} pertenece al grupo {area_code!r}, pero su polígono "
                    f"ya fue asignado completo al grupo {owner_code!r} por una fila más corta."
                )
            continue  # whole polygon already goes to this group

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
    for admin_idx, fine_feat in enumerate(fine_feats):
        label = get_feature_name(fine_feat) or f"#{admin_idx}"
        boundary = _project_department_boundary(
            fine_feat["geometry"], lon0, cos_lat0, scale, off_x, off_y, label
        )
        if boundary.is_empty:
            continue

        # 1. Claimed whole by a shallow CSV row.
        owner_code = land_owner.get(admin_idx)
        if owner_code is not None:
            geoms_by_area_code.setdefault(owner_code, []).append(boundary)
            continue

        # 2. No owner and no points: passes through under its own Land id.
        entries = by_department.get(admin_idx)
        if not entries:
            own_id = hierarchical_name_id(name_chains[admin_idx], str(admin_idx))
            geoms_by_area_code.setdefault(own_id, []).append(boundary)
            continue

        seeds = [e[0] for e in entries]
        area_codes = [e[1] for e in entries]

        # 3. Every point in this polygon shares one group: nothing to
        # partition, so the whole polygon goes to that group.
        if len(set(area_codes)) == 1:
            geoms_by_area_code.setdefault(area_codes[0], []).append(boundary)
            continue

        # 4. Mixed groups: bounded Voronoi split.
        cells = voronoi_cells_in_boundary(seeds, boundary)

        for cell, area_code in zip(cells, area_codes, strict=False):
            if cell is None or cell.is_empty:
                continue
            geoms_by_area_code.setdefault(area_code, []).append(cell)

    return {area_code: union_geoms(geoms) for area_code, geoms in geoms_by_area_code.items()}


def group_polygons_by_field(feats, geoms, code_field):
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
    return {code: union_geoms(g) for code, g in groups.items()}


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
