#!/usr/bin/env python3
"""country_to_svg_v2.py.

Convert TopoJSON administrative boundaries into a minimal, standardized SVG
for a single country.

Both input files MUST be TopoJSON (a single "topology" object with an
"arcs" array and one or more named objects under "objects"). Plain GeoJSON
is not accepted.

v2 design:
  - The FIRST file passed is the sole source of geometric truth. It can be
    any fine administrative level (county/ADM2/ADM3/...); the script does
    not care which level it is, only that its polygons are internally
    coherent (non-overlapping, no gaps to fill against each other).
  - The national border is simply the union of every polygon in that file.
  - The SECOND file (optional) is a coarser level (state/province/ADM1)
    used ONLY to group and name the fine-level polygons. Its own geometry
    is discarded once grouping is done — the reconstructed state border is
    the union of every fine-level polygon "mostly covered by" that state,
    AND the province borders rendered in the SVG are the true shared-edge
    arcs between two provinces, computed from the FINE file's own arc
    topology (grouped by province assignment), never from the coarse
    file's geometry.
  - "mostly covers": polygon A mostly-covers polygon B if
    area(A ∩ B) / area(B) >= threshold (default 0.8). Each fine-level
    polygon must be mostly-covered by exactly one coarse polygon; if none
    (or more than one plausible candidate in a genuinely ambiguous way)
    qualifies, we fail loudly with details, since with this dataset that
    indicates a real data mismatch rather than an expected edge case.

Pass files as: fine.topojson [coarse.topojson] country --min-island-percent PCT -o out.svg

This file only does argument parsing and orchestration: loading the right
levels (topo_io), running the assignment/clipping/pruning pipeline
(geometry), and assembling the final SVG string (svg_render). See those
modules for the actual logic.
"""

import argparse
import math
import pathlib
import sys
import time

from constants import BACKGROUND_COLOR, HEIGHT, MOSTLY_COVERS_THRESHOLD, PADDING, PRECISION
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
from shapely.ops import unary_union
from svg_render import (
    project_point,
    render_area_codes_group,
    render_ecoregions_group,
    render_fine_group,
    render_glaciers_group,
    render_lakes_group,
    render_national,
    render_points_group,
    render_province_group,
    render_roads_group,
)
from topo_io import (
    attach_level_names,
    classify_arcs_by_group,
    filter_points_by_extra,
    get_feature_name,
    load_extra_csv,
    load_features,
    load_geojson_features,
    load_geojson_line_features,
    load_geojson_point_features,
    load_raw_arc_features,
)
from voronoi import build_area_code_geometries, group_polygons_by_area_code, project_geometry


def main() -> None:
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
        "--ecoregions",
        default=None,
        metavar="PATH",
        help="Optional ecoregions TopoJSON. Each ecoregion polygon is clipped to the national border and added as its own 'Ecoregions' layer in the output SVG, filled with the land color.",
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
        "--points",
        default=None,
        metavar="PATH",
        help="Optional world points GeoJSON (plain GeoJSON, not TopoJSON, Point/MultiPoint features). Each point is matched to the fine-level administrative polygon that contains it and named as '<admin name chain>.<point name>' (point name resolved with the same field names as the Land layer). Added as its own 'Points' layer in the output SVG, invisible (no fill or stroke) and non-clickable.",
    )
    ap.add_argument(
        "--extra",
        default=None,
        metavar="PATH",
        help="Optional CSV (columns: AreaCode, Province, Department, Locality) restricting --points to only the points whose (Province, Department, Locality) identity appears in this file. Requires --points. Every CSV row must match a point, or the run fails; a point not listed in the CSV is silently dropped.",
    )
    ap.add_argument(
        "--roads",
        default=None,
        metavar="PATH",
        help="Optional world roads GeoJSON (plain GeoJSON, not TopoJSON, LineString/MultiLineString features rather than polygons). Each road is clipped to the national border and added as its own 'Roads' layer in the output SVG, stroked (no fill) in road color and non-clickable, rendered on top of every other layer.",
    )
    ap.add_argument(
        "--area-code-polygons",
        default=None,
        metavar="PATH",
        help="Optional area-code boundaries TopoJSON, as actual polygons (not point+--extra). Expanded via a bounded polygon-Voronoi so misalignment with the admin border becomes gap-filling/trimming at the true equidistant seam -- never a gap, never a spike -- then clipped to the national border and added as the 'AreaCodes' layer. Mutually exclusive with --extra.",
    )
    ap.add_argument(
        "--area-code-field",
        default="AREA_CODE",
        metavar="FIELD",
        help="Property name in --area-code-polygons holding each feature's area code. Default: AREA_CODE.",
    )
    args = ap.parse_args()

    if args.fine_level < args.coarse_level:
        sys.exit(
            f"Error: fine_level ({args.fine_level}) debe ser >= coarse_level ({args.coarse_level})."
        )

    if args.extra and not args.points:
        sys.exit(
            "Error: --extra requiere --points (no hay nada que filtrar sin un geojson de puntos)."
        )

    if args.area_code_polygons and args.extra:
        sys.exit(
            "Error: --area-code-polygons y --extra no pueden usarse juntos (ambos generan la capa AreaCodes)."
        )

    levels = list(range(args.coarse_level, args.fine_level + 1))
    fine_level = levels[-1]
    country_dir = pathlib.Path("maps") / args.country

    print(f"=== Cargando niveles administrativos {levels} para {args.country} ===", file=sys.stderr)
    feats_by_level = {}
    for level in levels:
        path = country_dir / f"ADM{level}.topojson"
        feats = load_features(path)
        attach_level_names(feats, level)
        feats_by_level[level] = feats

    fine_feats = feats_by_level[fine_level]

    if args.min_island_percent > 0:
        total_area = sum(geometry_total_area(f["geometry"]) for f in fine_feats)
        min_area = total_area * (args.min_island_percent / 100.0)
        other_feats = [f for level in levels[:-1] for f in feats_by_level[level]]
        removed = prune_small_edge_parts(fine_feats, other_feats, min_area)
        print(f"Removed {removed} small outer national polygon parts.", file=sys.stderr)

    t0 = time.perf_counter()

    geoms_by_level = {}
    kept_indices_by_level = {}
    for level in levels:
        kept_feats, kept_geoms, kept_indices = build_valid_geoms(
            feats_by_level[level], f"ADM{level}"
        )
        feats_by_level[level] = kept_feats
        geoms_by_level[level] = kept_geoms
        kept_indices_by_level[level] = kept_indices

    # Re-bind now that invalid features may have been dropped from the
    # fine level's list above (the earlier `fine_feats = ...` assignment,
    # used only for min-island pruning before geometries existed, is now
    # stale).
    fine_feats = feats_by_level[fine_level]
    fine_geoms = geoms_by_level[fine_level]

    print("=== Calculando borde nacional (unión de nivel fino) ===", file=sys.stderr)
    national_union = unary_union(fine_geoms)

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
            threshold=args.mostly_covers_threshold,
        )

    name_chains = []
    immediate_parent_name_by_fine_idx = [None] * len(fine_feats)
    for fi in range(len(fine_feats)):
        idx_at_level = {fine_level: fi}
        broken = False
        for i in range(len(levels) - 1, 0, -1):
            level, parent_level = levels[i], levels[i - 1]
            parent_idx = -1 if broken else assignments[level, parent_level][idx_at_level[level]]
            idx_at_level[parent_level] = parent_idx
            broken = broken or parent_idx == -1

        if len(levels) > 1:
            parent_level = levels[0]
            parent_idx = idx_at_level[parent_level]
            if parent_idx != -1:
                immediate_parent_name_by_fine_idx[fi] = get_feature_name(
                    feats_by_level[parent_level][parent_idx]
                )
        else:
            immediate_parent_name_by_fine_idx[fi] = get_feature_name(fine_feats[fi])

        chain = [
            get_feature_name(feats_by_level[level][idx_at_level[level]])
            if idx_at_level[level] != -1
            else None
            for level in levels
        ]
        name_chains.append(chain)

    ecoregion_pairs = []
    if args.ecoregions:
        print("\n=== Procesando ecorregiones ===", file=sys.stderr)
        national_bounds = national_union.bounds

        all_ecoregion_feats = load_features(args.ecoregions)
        ecoregion_feats = [
            feat
            for feat in all_ecoregion_feats
            if bounds_overlap(geometry_bounds(feat["geometry"]), national_bounds)
        ]
        print(
            f"  {len(ecoregion_feats)} de {len(all_ecoregion_feats)} ecorregiones del mundo pasan el "
            f"filtro de caja delimitadora y se procesan como candidatas.",
            file=sys.stderr,
        )

        ecoregion_feats, ecoregion_geoms, _ = build_valid_geoms(ecoregion_feats, "ecorregión")
        ecoregion_pairs = clip_features_to_national(
            ecoregion_feats, ecoregion_geoms, national_union
        )
        print(
            f"  {len(ecoregion_pairs)} de {len(ecoregion_feats)} candidatas intersectan realmente el país.",
            file=sys.stderr,
        )

    point_pairs = []
    if args.points:
        print("\n=== Procesando puntos ===", file=sys.stderr)

        all_point_feats = load_geojson_point_features(args.points)
        point_feats = all_point_feats

        if args.extra:
            print(
                f"  Filtrando con {args.extra} (contra los datos propios de cada punto, antes de cualquier procesamiento geométrico)...",
                file=sys.stderr,
            )
            extra_rows = load_extra_csv(args.extra)
            filtered_feats, failure = filter_points_by_extra(point_feats, extra_rows)
            if failure is not None:
                row = failure["row"]
                available = failure["available_localities"]
                hint = (
                    f" Localidades disponibles en el geojson para esa Provincia/Departamento: {available}."
                    if available
                    else " No hay ningún punto del geojson para esa Provincia/Departamento en absoluto."
                )
                sys.exit(
                    f"Error: la fila {row.get('AreaCode', '?')} de {args.extra} "
                    f"({row['Province']}/{row['Department']}/{row['Locality']}) "
                    f"no coincide con ningún punto del geojson.{hint}"
                )
            point_feats = filtered_feats
            print(
                f"  {len(point_feats)} de {len(all_point_feats)} puntos conservados tras aplicar "
                f"{args.extra} ({len(extra_rows)} filas).",
                file=sys.stderr,
            )

        national_bounds = national_union.bounds
        point_feats = [
            feat
            for feat in point_feats
            if bounds_overlap(point_geometry_bounds(feat["geometry"]), national_bounds)
        ]
        print(
            f"  {len(point_feats)} puntos pasan el filtro de caja delimitadora y se procesan como candidatos.",
            file=sys.stderr,
        )

        point_feats, point_geoms, _ = build_valid_geoms(point_feats, "punto")
        point_pairs = match_points_to_polygons(point_feats, point_geoms, fine_geoms)
        matched = sum(1 for _, admin_idx in point_pairs if admin_idx is not None)
        print(
            f"  {matched} de {len(point_feats)} candidatos caen dentro de un polígono administrativo "
            f"(el resto no coincide con ningún polígono y se omite).",
            file=sys.stderr,
        )

    glacier_pairs = []
    if args.glaciers:
        print("\n=== Procesando glaciares ===", file=sys.stderr)
        national_bounds = national_union.bounds

        all_glacier_feats = load_geojson_features(args.glaciers)
        glacier_feats = [
            feat
            for feat in all_glacier_feats
            if bounds_overlap(geometry_bounds(feat["geometry"]), national_bounds)
        ]
        print(
            f"  {len(glacier_feats)} de {len(all_glacier_feats)} glaciares del mundo pasan el "
            f"filtro de caja delimitadora y se procesan como candidatos.",
            file=sys.stderr,
        )

        glacier_feats, glacier_geoms, _ = build_valid_geoms(glacier_feats, "glacier")
        glacier_pairs = clip_features_to_national(glacier_feats, glacier_geoms, national_union)
        print(
            f"  {len(glacier_pairs)} de {len(glacier_feats)} candidatos intersectan realmente el país.",
            file=sys.stderr,
        )

    lake_pairs = []
    if args.lakes:
        print("\n=== Procesando lagos ===", file=sys.stderr)
        national_bounds = national_union.bounds

        all_lake_feats = load_geojson_features(args.lakes)
        lake_feats = [
            feat
            for feat in all_lake_feats
            if bounds_overlap(geometry_bounds(feat["geometry"]), national_bounds)
        ]
        print(
            f"  {len(lake_feats)} de {len(all_lake_feats)} lagos del mundo pasan el "
            f"filtro de caja delimitadora y se procesan como candidatos.",
            file=sys.stderr,
        )

        lake_feats, lake_geoms, _ = build_valid_geoms(lake_feats, "lago")
        lake_pairs = clip_features_to_national(lake_feats, lake_geoms, national_union)
        print(
            f"  {len(lake_pairs)} de {len(lake_feats)} candidatos intersectan realmente el país.",
            file=sys.stderr,
        )

    road_pairs = []
    if args.roads:
        print("\n=== Procesando rutas ===", file=sys.stderr)
        national_bounds = national_union.bounds

        all_road_feats = load_geojson_line_features(args.roads)
        road_feats = [
            feat
            for feat in all_road_feats
            if bounds_overlap(line_geometry_bounds(feat["geometry"]), national_bounds)
        ]
        print(
            f"  {len(road_feats)} de {len(all_road_feats)} rutas del mundo pasan el "
            f"filtro de caja delimitadora y se procesan como candidatas.",
            file=sys.stderr,
        )

        road_feats, road_geoms, _ = build_valid_geoms(road_feats, "ruta")
        road_pairs = clip_lines_to_national(road_feats, road_geoms, national_union)
        print(
            f"  {len(road_pairs)} de {len(road_feats)} candidatas intersectan realmente el país.",
            file=sys.stderr,
        )

    print(f"\n✔ Completado en {time.perf_counter() - t0:.2f}s\n", file=sys.stderr)

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
    width, off_x, off_y = (
        raw_w * scale + 2 * PADDING,
        PADDING - min(xs) * scale,
        PADDING - min(ys) * scale,
    )

    area_code_geoms = {}
    if args.extra:
        print(
            "\n=== Calculando códigos de área (Voronoi centroidal acotado por departamento) ===",
            file=sys.stderr,
        )
        area_code_geoms = build_area_code_geometries(
            point_pairs, fine_feats, extra_rows, lon0, cos_lat0, scale, off_x, off_y
        )
        print(f"  {len(area_code_geoms)} código(s) de área generados.", file=sys.stderr)
    elif args.area_code_polygons:
        print(
            "\n=== Procesando polígonos de códigos de área (sin ajuste, tal cual) ===",
            file=sys.stderr,
        )
        ac_feats = load_features(args.area_code_polygons)
        ac_feats, ac_geoms, _ = build_valid_geoms(ac_feats, "código de área")
        grouped = group_polygons_by_area_code(ac_feats, ac_geoms, args.area_code_field)
        print(
            f"  {len(grouped)} código(s) de área agrupados desde {len(ac_feats)} polígono(s).",
            file=sys.stderr,
        )

        area_code_geoms = {
            code: project_geometry(geom, lon0, cos_lat0, scale, off_x, off_y)
            for code, geom in grouped.items()
        }

    layers = []
    layers.append(
        render_fine_group(fine_feats, name_chains, "Land", lon0, cos_lat0, scale, off_x, off_y)
    )
    if glacier_pairs:
        layers.append(
            render_glaciers_group(glacier_pairs, "Glaciers", lon0, cos_lat0, scale, off_x, off_y)
        )
    if lake_pairs:
        layers.append(render_lakes_group(lake_pairs, "Lakes", lon0, cos_lat0, scale, off_x, off_y))

    fine_path = country_dir / f"ADM{fine_level}.topojson"
    fine_raw_feats, fine_raw_decoded_arcs = load_raw_arc_features(fine_path)
    # load_raw_arc_features() re-parses the original file from scratch, so
    # it still has every feature, including ones we dropped above for
    # having an irreparable geometry. Keep only the ones that survived, in
    # the same order, so this lines up with immediate_parent_name_by_fine_idx
    # (built over the already-filtered fine_feats).
    fine_raw_feats = [fine_raw_feats[i] for i in kept_indices_by_level[fine_level]]
    pair_arcs = classify_arcs_by_group(
        fine_raw_feats, immediate_parent_name_by_fine_idx, source_label="nivel fino"
    )
    layers.append(
        render_province_group(
            pair_arcs, fine_raw_decoded_arcs, "Provinces", lon0, cos_lat0, scale, off_x, off_y
        )
    )

    if point_pairs:
        layers.append(
            render_points_group(
                point_pairs, name_chains, "Points", lon0, cos_lat0, scale, off_x, off_y
            )
        )

    if area_code_geoms:
        layers.append(render_area_codes_group(area_code_geoms, "AreaCodes"))

    if ecoregion_pairs:
        layers.append(
            render_ecoregions_group(
                ecoregion_pairs, "Ecoregions", lon0, cos_lat0, scale, off_x, off_y
            )
        )

    layers.append(
        render_national(national_union, args.country, lon0, cos_lat0, scale, off_x, off_y)
    )

    if road_pairs:
        layers.append(render_roads_group(road_pairs, "Roads", lon0, cos_lat0, scale, off_x, off_y))

    w, h = round(width, PRECISION), round(HEIGHT, PRECISION)
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<rect id="Background" width="{w}" height="{h}" fill="{BACKGROUND_COLOR}"/>'
        + "".join(layers)
        + "</svg>"
    )

    out_path = args.output or f"{args.country.replace(' ', '_')}.svg"
    pathlib.Path(out_path).write_text(svg, encoding="utf-8")
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
