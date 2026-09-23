"""Fill overlay-polygon gaps along the coastline (or any boundary) where
the overlay's own coastline doesn't exactly match Land's.

Problem: an --overlay-polygons dataset (ecoregions, area codes, ...) has
its own idea of where the coast is. Clipping the overlay to the national
border alone leaves a sliver of national territory belonging to no
overlay group at all wherever the two coastlines disagree.

Fix: take the gap (national_union - overlay_union), sample it with a grid
of points spaced `spacing` apart (so a long thin gap gets more than one
point, and isn't swallowed whole by a single nearest-polygon lookup),
assign each point to its nearest overlay group, then run ONE Voronoi
partition of the gap using those points as seeds -- clipped to the gap
itself, never to the individual overlay polygons -- and union each
resulting cell into its assigned group.

This is deliberately NOT a Voronoi of the overlay polygons themselves
(the original, scrapped approach): partitioning every polygon produced
spiky results. Only the gap area is ever partitioned, and only once (no
Lloyd's-algorithm relaxation), since the grid seeding already follows the
gap's shape.
"""

from geometry import extract_polygons, to_multi_or_single_polygon
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree
from voronoi import voronoi_cells_in_boundary


def _grid_points(polygon, spacing):
    minx, miny, maxx, maxy = polygon.bounds
    if maxx <= minx or maxy <= miny:
        return [polygon.representative_point()]

    prepared = prep(polygon)
    nx = int((maxx - minx) / spacing) + 1
    ny = int((maxy - miny) / spacing) + 1

    points = []
    for i in range(nx + 1):
        x = minx + i * spacing
        for j in range(ny + 1):
            y = miny + j * spacing
            pt = Point(x, y)
            if prepared.covers(pt):
                points.append(pt)

    return points or [polygon.representative_point()]


def _sample_gap_points(gap_geom, spacing):
    points = []
    for polygon in extract_polygons(gap_geom):
        points.extend(_grid_points(polygon, spacing))
    return points


def _break_apart_union(geom):
    """Inkscape's Break Apart -> Union: split into individual polygons,
    then re-union them.
    """
    polys = extract_polygons(geom)
    if not polys:
        return geom
    return unary_union(polys)


def fill_overlay_gaps(group_geoms, national_union, spacing):
    """group_geoms: {name: geometry}, overlay groups already clipped/unioned,
    in the SAME projected space as national_union. Returns a new dict with
    any gap between the overlay's coverage and Land assigned to whichever
    overlay group is spatially closest, so the overlay's outer edge ends
    up following Land's own coastline instead of its own.
    """
    if not group_geoms:
        return group_geoms

    overlay_union = unary_union(list(group_geoms.values()))
    raw_gap = national_union.difference(overlay_union)
    gap = to_multi_or_single_polygon(extract_polygons(raw_gap))
    if gap.is_empty:
        return group_geoms

    points = _sample_gap_points(gap, spacing)
    if not points:
        return group_geoms

    names = list(group_geoms.keys())
    tree = STRtree(list(group_geoms.values()))

    assigned_name = [names[int(tree.nearest(pt))] for pt in points]

    seeds = [(pt.x, pt.y) for pt in points]
    cells = voronoi_cells_in_boundary(seeds, gap)

    additions = {}
    for cell, name in zip(cells, assigned_name, strict=False):
        if cell is None or cell.is_empty:
            continue
        additions.setdefault(name, []).append(cell)

    filled = dict(group_geoms)
    for name, cell_list in additions.items():
        filled[name] = _break_apart_union(unary_union([filled[name], *cell_list]))

    return filled
