from geometry import extract_polygons, to_multi_or_single_polygon
from shapely.geometry import Point
from shapely.ops import unary_union
from shapely.prepared import prep
from shapely.strtree import STRtree
from voronoi import union_geoms, voronoi_cells_in_boundary


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
    polys = extract_polygons(geom)
    if not polys:
        return geom
    return union_geoms(polys)


def fill_overlay_gaps(group_geoms, national_union, spacing):
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
        filled[name] = _break_apart_union(union_geoms([filled[name], *cell_list]))

    return filled
