#!/usr/bin/env python3
"""country_to_svg_v2.py.

Convert TopoJSON administrative boundaries into a minimal, standardized SVG
for a single country.

Both admin input files MUST be TopoJSON (a single "topology" object with an
"arcs" array and one or more named objects under "objects"). Plain GeoJSON
is not accepted for admin levels.

v2 design:
  - The FINEST loaded level is the sole source of geometric truth. The
    national border is the union of its polygons.
  - Coarser levels are used ONLY to group and name the fine-level polygons
    ("mostly covers" assignment). Province borders in the SVG are the true
    shared-edge arcs of the fine file's own topology.
  - --group CSV (GroupName,Level1,...,LevelN) turns the Land layer into one
    path per group.
  - --overlay-polygons loads any other TopoJSON that fills the national
    area with its own polygons (ecoregions, area codes, or anything else
    of that shape), optionally grouped by --overlay-field, and renders it
    as its own "Overlay" layer.
"""

import argparse
import math
import pathlib
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from constants import (
    COUNTRIES_COLORS,
    HEIGHT,
    MOSTLY_COVERS_THRESHOLD,
    OVERLAY_GAP_POINT_SPACING,
    PADDING,
    PRECISION,
)
from geometry import (
    bounds_overlap,
    build_valid_geoms,
    clip_features_to_national,
    clip_lines_to_national,
    flatten_coords,
    geometry_bounds,
    geometry_total_area,
    get_national_frame,
    group_fine_by_coarse,
    line_geometry_bounds,
    match_points_to_polygons,
    point_geometry_bounds,
    prune_small_edge_parts,
)
from grouping import build_land_group_geometries
from overlay_fill import fill_overlay_gaps
from shapely.ops import unary_union
from svg_render import (
    project_point,
    render_area_codes_group,
    render_fine_group,
    render_glaciers_group,
    render_lakes_group,
    render_national,
    render_national_parks_group,
    render_points_group,
    render_province_group,
    render_roads_group,
)
from topo_io import (
    attach_level_names,
    classify_arcs_by_group,
    filter_points_by_group,
    get_feature_name,
    group_depth,
    load_features,
    load_geojson_features,
    load_geojson_line_features,
    load_geojson_point_features,
    load_group_csv,
    load_raw_arc_features,
)
from voronoi import build_area_code_geometries, project_geometry

# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class Admin:
    """Everything derived from the loaded administrative levels."""

    levels: list
    fine_level: int
    fine_feats: list
    fine_geoms: list
    kept_fine_indices: list
    national_union: Any
    name_chains: list
    parent_names: list


@dataclass
class Projection:
    """Lon/lat -> SVG pixel transform plus the resulting canvas width."""

    lon0: float
    cos_lat0: float
    scale: float
    off_x: float
    off_y: float
    width: float

    @property
    def args(self):
        return (self.lon0, self.cos_lat0, self.scale, self.off_x, self.off_y)


@dataclass
class Overlays:
    glaciers: list = field(default_factory=list)
    lakes: list = field(default_factory=list)
    national_parks: list = field(default_factory=list)
    roads: list = field(default_factory=list)
    points: list = field(default_factory=list)


@dataclass
class OverlaySpec:
    """How to load, filter and clip one kind of overlay layer."""

    title: str
    plural: str
    label: str
    loader: Callable
    bounds_fn: Callable
    clip_fn: Callable
    feminine: bool = False


GLACIERS = OverlaySpec(
    "glaciares",
    "glaciares",
    "glacier",
    load_geojson_features,
    geometry_bounds,
    clip_features_to_national,
)
LAKES = OverlaySpec(
    "lagos", "lagos", "lago", load_geojson_features, geometry_bounds, clip_features_to_national
)
NATIONAL_PARKS = OverlaySpec(
    "parques nacionales",
    "parques nacionales",
    "parque nacional",
    load_geojson_features,
    geometry_bounds,
    clip_features_to_national,
)
ROADS = OverlaySpec(
    "rutas",
    "rutas",
    "ruta",
    load_geojson_line_features,
    line_geometry_bounds,
    clip_lines_to_national,
    feminine=True,
)


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "country",
        help="Country directory name under maps/, e.g. SouthKorea. Files are read from maps/<country>/ADM<level>.topojson.",
    )
    ap.add_argument("coarse_level", type=int, help="Coarsest admin level to load, e.g. 1 for ADM1.")
    ap.add_argument(
        "fine_level",
        type=int,
        help="Finest admin level to load. This level's file is the sole source of geometric truth (national border = union of its polygons).",
    )
    ap.add_argument("-o", "--output")
    ap.add_argument(
        "--min-island-percent",
        type=float,
        default=0,
        metavar="PCT",
        help="Drop disconnected polygon parts smaller than this percent of the total national area, if they touch the outer national bounding frame.",
    )
    ap.add_argument(
        "--mostly-covers-threshold",
        type=float,
        default=MOSTLY_COVERS_THRESHOLD,
        metavar="FRAC",
        help="Fraction (0-1) of a finer polygon's own area that must lie inside a coarser polygon to count as belonging to it. Default 0.5.",
    )
    ap.add_argument(
        "--glaciers",
        default=None,
        metavar="PATH",
        help="Optional world glaciers GeoJSON (plain GeoJSON, not TopoJSON). Each glacier polygon is clipped to the national border and added as its own 'Glaciers' layer in the output SVG, filled white and non-clickable, rendered above the Land layer but below the administrative border layers (Provinces/Country).",
    )
    ap.add_argument(
        "--lakes",
        default=None,
        metavar="PATH",
        help="Optional world lakes GeoJSON (plain GeoJSON, not TopoJSON). Each lake polygon is clipped to the national border and added as its own 'Lakes' layer in the output SVG, filled with the background color and non-clickable, rendered above the Land layer but below the administrative border layers (Provinces/Country).",
    )
    ap.add_argument(
        "--national-parks",
        default=None,
        metavar="PATH",
        help="Optional world national parks GeoJSON (plain GeoJSON, not TopoJSON). Each park polygon is clipped to the national border and added as its own 'NationalParks' layer in the output SVG, filled with the land color and clickable (no pointer-events: none), rendered above the Country layer but below Roads.",
    )
    ap.add_argument(
        "--points",
        default=None,
        metavar="PATH",
        help="Optional world points GeoJSON (plain GeoJSON, not TopoJSON, Point/MultiPoint features). Each point is matched to the fine-level administrative polygon that contains it and named as '<admin name chain>.<point name>' (point name resolved with the same field names as the Land layer). Added as its own 'Points' layer in the output SVG, invisible (no fill or stroke) and non-clickable. With --group, also used as Voronoi seeds when the CSV is deeper than the Land levels.",
    )
    ap.add_argument(
        "--group",
        default=None,
        metavar="PATH",
        help="Optional CSV (GroupName,Level1,...,LevelN) grouping features into named groups; the Land layer becomes one path per group. "
        "N <= Land depth: group Land polygons by the first N levels (warns if N < depth). "
        "N > Land depth: requires --points, used as Voronoi seeds.",
    )
    ap.add_argument(
        "--roads",
        default=None,
        metavar="PATH",
        help="Optional world roads GeoJSON (plain GeoJSON, not TopoJSON, LineString/MultiLineString features rather than polygons). Each road is clipped to the national border and added as its own 'Roads' layer in the output SVG, stroked (no fill) in road color and non-clickable, rendered on top of every other layer.",
    )
    ap.add_argument(
        "--overlay-polygons",
        default=None,
        metavar="PATH",
        help="Optional TopoJSON of polygons that fill (or mostly fill) the national area -- ecoregions, "
        "area codes, or anything else of that shape. If --overlay-field names a property, features "
        "sharing a value are unioned into one path per value; otherwise each feature becomes its own "
        "path. Added as its own 'Overlay' layer. Mutually exclusive with --group.",
    )
    ap.add_argument(
        "--overlay-field",
        default=None,
        metavar="NAME",
        help="Display name for the --overlay-polygons layer in the output SVG (e.g. 'Ecoregions', "
        "'AreaCodes'). Purely cosmetic -- has no effect on how features are grouped. Default: "
        "'Overlay'.",
    )
    ap.parse_args() if False else None  # placeholder, replaced below
    return ap


def validate_args(args) -> None:
    if args.fine_level < args.coarse_level:
        sys.exit(
            f"Error: fine_level ({args.fine_level}) debe ser >= coarse_level ({args.coarse_level})."
        )
    if args.overlay_polygons and args.group:
        sys.exit("Error: --overlay-polygons y --group no pueden usarse juntos.")


# ---------------------------------------------------------------------------
# --group mode selection
# ---------------------------------------------------------------------------


def warn_group_depth(n_levels, land_depth, has_points) -> None:
    if n_levels < land_depth:
        print(
            f"⚠ --group tiene {n_levels} niveles y Land {land_depth}: se agrupa por los "
            f"primeros {n_levels} y se ignoran los niveles más profundos.",
            file=sys.stderr,
        )
    if has_points:
        print(
            "⚠ --points no se usa para agrupar (Land alcanza la profundidad del CSV).",
            file=sys.stderr,
        )


def resolve_group_mode(args, levels):
    """Return (group_rows, group_mode); group_mode is None, "land" or "points"."""
    if not args.group:
        return None, None

    rows = load_group_csv(args.group)
    n_levels = group_depth(rows)
    # CSV Level1 is ADM1, so ADM0 does not count as a Land level.
    land_depth = len(levels) - (1 if args.coarse_level == 0 else 0)

    if n_levels > land_depth:
        if not args.points:
            sys.exit(
                f"Error: --group tiene {n_levels} niveles pero Land tiene {land_depth}. "
                f"Pasá --points o usá un Land con {n_levels} niveles."
            )
        return rows, "points"

    warn_group_depth(n_levels, land_depth, bool(args.points))
    return rows, "land"


# ---------------------------------------------------------------------------
# Administrative levels
# ---------------------------------------------------------------------------


def load_level_features(country_dir, levels):
    feats_by_level = {}
    for level in levels:
        feats = load_features(country_dir / f"ADM{level}.topojson")
        attach_level_names(feats, level)
        feats_by_level[level] = feats
    return feats_by_level


def prune_islands(feats_by_level, levels, percent) -> None:
    if percent <= 0:
        return
    fine_feats = feats_by_level[levels[-1]]
    total_area = sum(geometry_total_area(f["geometry"]) for f in fine_feats)
    min_area = total_area * (percent / 100.0)
    other_feats = [f for level in levels[:-1] for f in feats_by_level[level]]
    removed = prune_small_edge_parts(fine_feats, other_feats, min_area)
    print(f"Removed {removed} small outer national polygon parts.", file=sys.stderr)


def validate_level_geoms(feats_by_level, levels):
    """Build shapely geometries for every level, dropping unrepairable
    features. Returns (geoms_by_level, kept_indices_by_level); feats_by_level
    is updated in place with the surviving features.
    """
    geoms_by_level, kept_indices_by_level = {}, {}
    for level in levels:
        kept_feats, kept_geoms, kept_indices = build_valid_geoms(
            feats_by_level[level], f"ADM{level}"
        )
        feats_by_level[level] = kept_feats
        geoms_by_level[level] = kept_geoms
        kept_indices_by_level[level] = kept_indices
    return geoms_by_level, kept_indices_by_level


def assign_parents(feats_by_level, geoms_by_level, levels, threshold):
    assignments = {}
    for i in range(len(levels) - 1, 0, -1):
        level, parent_level = levels[i], levels[i - 1]
        print(
            f"\n=== Asignando ADM{level} a ADM{parent_level} (mostly_covers) ===", file=sys.stderr
        )
        assignments[level, parent_level] = group_fine_by_coarse(
            feats_by_level[level],
            geoms_by_level[level],
            feats_by_level[parent_level],
            geoms_by_level[parent_level],
            threshold=threshold,
        )
    return assignments


def indices_at_levels(fine_idx, levels, assignments):
    """Walk one fine polygon up the hierarchy: {level: feature index or -1}."""
    idx_at_level = {levels[-1]: fine_idx}
    broken = False
    for i in range(len(levels) - 1, 0, -1):
        level, parent_level = levels[i], levels[i - 1]
        parent_idx = -1 if broken else assignments[level, parent_level][idx_at_level[level]]
        idx_at_level[parent_level] = parent_idx
        broken = broken or parent_idx == -1
    return idx_at_level


def name_at(feats_by_level, level, idx):
    return get_feature_name(feats_by_level[level][idx]) if idx != -1 else None


def immediate_parent_name(feats_by_level, levels, fine_feat, idx_at_level):
    if len(levels) == 1:
        return get_feature_name(fine_feat)
    return name_at(feats_by_level, levels[0], idx_at_level[levels[0]])


def build_name_chains(feats_by_level, levels, assignments):
    fine_feats = feats_by_level[levels[-1]]
    name_chains, parent_names = [], []
    for fi, fine_feat in enumerate(fine_feats):
        idx_at_level = indices_at_levels(fi, levels, assignments)
        parent_names.append(immediate_parent_name(feats_by_level, levels, fine_feat, idx_at_level))
        name_chains.append([name_at(feats_by_level, lv, idx_at_level[lv]) for lv in levels])
    return name_chains, parent_names


def load_admin(args, levels, country_dir) -> Admin:
    fine_level = levels[-1]
    feats_by_level = load_level_features(country_dir, levels)
    prune_islands(feats_by_level, levels, args.min_island_percent)
    geoms_by_level, kept_indices_by_level = validate_level_geoms(feats_by_level, levels)

    print("=== Calculando borde nacional (unión de nivel fino) ===", file=sys.stderr)
    fine_geoms = geoms_by_level[fine_level]
    national_union = unary_union(fine_geoms)

    assignments = assign_parents(
        feats_by_level, geoms_by_level, levels, args.mostly_covers_threshold
    )
    name_chains, parent_names = build_name_chains(feats_by_level, levels, assignments)

    return Admin(
        levels=levels,
        fine_level=fine_level,
        fine_feats=feats_by_level[fine_level],
        fine_geoms=fine_geoms,
        kept_fine_indices=kept_indices_by_level[fine_level],
        national_union=national_union,
        name_chains=name_chains,
        parent_names=parent_names,
    )


# ---------------------------------------------------------------------------
# Overlays (glaciers, lakes, roads, points)
# ---------------------------------------------------------------------------


def load_clipped_overlay(spec, path, national_union):
    print(f"\n=== Procesando {spec.title} ===", file=sys.stderr)
    cand = "candidatas" if spec.feminine else "candidatos"

    all_feats = spec.loader(path)
    national_bounds = national_union.bounds
    candidates = [
        f for f in all_feats if bounds_overlap(spec.bounds_fn(f["geometry"]), national_bounds)
    ]
    print(
        f"  {len(candidates)} de {len(all_feats)} {spec.plural} del mundo pasan el "
        f"filtro de caja delimitadora y se procesan como {cand}.",
        file=sys.stderr,
    )

    feats, geoms, _ = build_valid_geoms(candidates, spec.label)
    pairs = spec.clip_fn(feats, geoms, national_union)
    print(
        f"  {len(pairs)} de {len(feats)} {cand} intersectan realmente el país.",
        file=sys.stderr,
    )
    return pairs


def load_point_pairs(args, admin, group_mode, group_rows):
    print("\n=== Procesando puntos ===", file=sys.stderr)
    all_feats = load_geojson_point_features(args.points)
    feats = all_feats

    if group_mode == "points":
        print(
            f"  Filtrando con {args.group} (contra los datos propios de cada punto, antes de cualquier procesamiento geométrico)...",
            file=sys.stderr,
        )
        feats, _ = filter_points_by_group(feats, group_rows)
        print(
            f"  {len(feats)} de {len(all_feats)} puntos conservados tras aplicar "
            f"{args.group} ({len(group_rows)} filas).",
            file=sys.stderr,
        )

    national_bounds = admin.national_union.bounds
    feats = [
        f for f in feats if bounds_overlap(point_geometry_bounds(f["geometry"]), national_bounds)
    ]
    print(
        f"  {len(feats)} puntos pasan el filtro de caja delimitadora y se procesan como candidatos.",
        file=sys.stderr,
    )

    feats, geoms, _ = build_valid_geoms(feats, "punto")
    pairs = match_points_to_polygons(feats, geoms, admin.fine_geoms)
    matched = sum(1 for _, admin_idx in pairs if admin_idx is not None)
    print(
        f"  {matched} de {len(feats)} candidatos caen dentro de un polígono administrativo "
        f"(el resto no coincide con ningún polígono y se omite).",
        file=sys.stderr,
    )
    return pairs


def load_overlays(args, admin, group_mode, group_rows) -> Overlays:
    union = admin.national_union
    overlays = Overlays()
    if args.points:
        overlays.points = load_point_pairs(args, admin, group_mode, group_rows)
    if args.glaciers:
        overlays.glaciers = load_clipped_overlay(GLACIERS, args.glaciers, union)
    if args.lakes:
        overlays.lakes = load_clipped_overlay(LAKES, args.lakes, union)
    if args.national_parks:
        overlays.national_parks = load_clipped_overlay(NATIONAL_PARKS, args.national_parks, union)
    if args.roads:
        overlays.roads = load_clipped_overlay(ROADS, args.roads, union)
    return overlays


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def compute_projection(fine_feats) -> Projection:
    frame = get_national_frame(fine_feats)
    lon0, lat0 = (frame[0] + frame[2]) / 2, (frame[1] + frame[3]) / 2
    cos_lat0 = math.cos(math.radians(lat0))

    xs, ys = zip(
        *[
            project_point(pt[0], pt[1], lon0, cos_lat0)
            for feat in fine_feats
            for pt in flatten_coords(feat["geometry"])
        ],
        strict=False,
    )
    raw_w, raw_h = max(xs) - min(xs), max(ys) - min(ys)
    scale = (HEIGHT - 2 * PADDING) / raw_h if raw_h else 1
    return Projection(
        lon0=lon0,
        cos_lat0=cos_lat0,
        scale=scale,
        off_x=PADDING - min(xs) * scale,
        off_y=PADDING - min(ys) * scale,
        width=raw_w * scale + 2 * PADDING,
    )


# ---------------------------------------------------------------------------
# Groups (the Land layer under --group, or the Overlay layer)
# ---------------------------------------------------------------------------


def project_all(geoms_by_name, proj):
    return {name: project_geometry(geom, *proj.args) for name, geom in geoms_by_name.items()}


def groups_from_points(admin, group_rows, overlays, proj):
    print(
        "\n=== Calculando grupos (Voronoi centroidal acotado por polígono de Land) ===",
        file=sys.stderr,
    )
    geoms = build_area_code_geometries(overlays.points, admin.fine_feats, group_rows, *proj.args)
    print(f"  {len(geoms)} grupo(s) generados.", file=sys.stderr)
    return geoms


def groups_from_land(args, admin, group_rows, proj):
    print("\n=== Agrupando polígonos de Land según --group ===", file=sys.stderr)
    grouped = build_land_group_geometries(
        group_rows, admin.name_chains, admin.fine_geoms, args.coarse_level
    )
    geoms = project_all(grouped, proj)
    print(f"  {len(geoms)} grupo(s) generados.", file=sys.stderr)
    return geoms


def groups_from_overlay_polygons(args, admin, proj):
    print("\n=== Procesando --overlay-polygons ===", file=sys.stderr)
    national_union = admin.national_union
    national_bounds = national_union.bounds

    all_feats = load_features(args.overlay_polygons)
    feats = [
        f for f in all_feats if bounds_overlap(geometry_bounds(f["geometry"]), national_bounds)
    ]
    print(
        f"  {len(feats)} de {len(all_feats)} polígono(s) del mundo pasan el "
        f"filtro de caja delimitadora y se procesan como candidatos.",
        file=sys.stderr,
    )

    feats, geoms, _ = build_valid_geoms(feats, "overlay")
    pairs = clip_features_to_national(feats, geoms, national_union)
    print(
        f"  {len(pairs)} de {len(feats)} candidatos intersectan realmente el país.",
        file=sys.stderr,
    )

    grouped = {}
    for i, (feat, geom) in enumerate(pairs):
        name = get_feature_name(feat) or str(i)
        grouped.setdefault(name, []).append(geom)
    grouped = {name: unary_union(geoms) for name, geoms in grouped.items()}
    print(
        f"  {len(grouped)} nombre(s) distinto(s) agrupados desde {len(pairs)} polígono(s).",
        file=sys.stderr,
    )

    projected = project_all(grouped, proj)

    print("  Rellenando huecos costeros contra el borde nacional...", file=sys.stderr)
    national_projected = project_geometry(national_union, *proj.args)
    return fill_overlay_gaps(projected, national_projected, OVERLAY_GAP_POINT_SPACING)


def build_group_geoms(args, admin, group_mode, group_rows, overlays, proj):
    if group_mode == "points":
        return groups_from_points(admin, group_rows, overlays, proj)
    if group_mode == "land":
        return groups_from_land(args, admin, group_rows, proj)
    if args.overlay_polygons:
        return groups_from_overlay_polygons(args, admin, proj)
    return {}


# ---------------------------------------------------------------------------
# Layers / SVG
# ---------------------------------------------------------------------------


def render_land_layer(args, admin, group_mode, group_geoms, proj):
    if group_mode:
        return render_area_codes_group(args.country, group_geoms, "Land")
    if args.overlay_polygons:
        layer_id = args.overlay_field or "Overlay"
        return render_area_codes_group(args.country, group_geoms, layer_id)
    return render_fine_group(args.country, admin.fine_feats, admin.name_chains, "Land", *proj.args)


def render_points_layer(overlays, group_mode, admin, proj):
    # In "points" group mode the points only exist to seed the Voronoi
    # split; they're consumed by build_group_geoms and shouldn't also
    # appear as their own layer.
    if group_mode == "points":
        return ""
    return render_points_group(overlays.points, admin.name_chains, "Points", *proj.args)


def render_provinces_layer(args, admin, country_dir, proj):
    fine_path = country_dir / f"ADM{admin.fine_level}.topojson"
    raw_feats, decoded_arcs = load_raw_arc_features(fine_path)
    # load_raw_arc_features() re-parses the file from scratch, so it still
    # has features dropped earlier for irreparable geometry. Keep only the
    # survivors, in order, so it lines up with admin.parent_names.
    raw_feats = [raw_feats[i] for i in admin.kept_fine_indices]
    pair_arcs = classify_arcs_by_group(raw_feats, admin.parent_names, source_label="nivel fino")
    return render_province_group(pair_arcs, decoded_arcs, "Provinces", *proj.args)


def build_layers(args, admin, group_mode, group_geoms, overlays, proj, country_dir):
    """Layers in bottom-to-top drawing order."""
    return [
        render_land_layer(args, admin, group_mode, group_geoms, proj),
        render_glaciers_group(overlays.glaciers, "Glaciers", *proj.args),
        render_lakes_group(args.country, overlays.lakes, "Lakes", *proj.args),
        render_provinces_layer(args, admin, country_dir, proj),
        render_points_layer(overlays, group_mode, admin, proj),
        render_national(admin.national_union, args.country, *proj.args),
        render_national_parks_group(
            args.country, overlays.national_parks, "NationalParks", *proj.args
        ),
        render_roads_group(overlays.roads, "Roads", *proj.args),
    ]


def assemble_svg(layers, width, country: str) -> str:
    w, h = round(width, PRECISION), round(HEIGHT, PRECISION)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<rect id="Background" width="{w}" height="{h}" fill="{COUNTRIES_COLORS[country]["water_color"]}"/>'
        + "".join(layers)
        + "</svg>"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    ap = build_parser()
    args = ap.parse_args()
    validate_args(args)

    levels = list(range(args.coarse_level, args.fine_level + 1))
    country_dir = pathlib.Path("maps") / args.country
    group_rows, group_mode = resolve_group_mode(args, levels)

    print(f"=== Cargando niveles administrativos {levels} para {args.country} ===", file=sys.stderr)
    t0 = time.perf_counter()
    admin = load_admin(args, levels, country_dir)
    overlays = load_overlays(args, admin, group_mode, group_rows)

    proj = compute_projection(admin.fine_feats)
    group_geoms = build_group_geoms(args, admin, group_mode, group_rows, overlays, proj)
    layers = build_layers(args, admin, group_mode, group_geoms, overlays, proj, country_dir)

    out_path = args.output or f"{args.country.replace(' ', '_')}.svg"
    pathlib.Path(out_path).write_text(
        assemble_svg(layers, proj.width, args.country), encoding="utf-8"
    )

    print(f"\n✔ Completado en {time.perf_counter() - t0:.2f}s\n", file=sys.stderr)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
