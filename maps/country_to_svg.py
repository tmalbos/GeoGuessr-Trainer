#!/usr/bin/env python3
"""country_to_svg.py.

Convert GeoJSON administrative boundaries into a minimal, standardized SVG
for a single country, in a single call. No simplification is applied -
coordinates are only rounded for output size, which doesn't change the geometry.

Pass files in order: national.json [province.json] [extra1.json extra2.json ...]
Each extra file is one level deeper than the last (counties, then
municipalities, then whatever else a country happens to use).

--expand NAME [NAME ...]
    Names can be a province, or anything in any extra file - the script
    searches all of them. Naming something opens exactly the ancestor chain
    needed to reach it (so naming one county opens its state down to county
    level, not further, and leaves every other state untouched), then drills
    that one entity into its own children.

    Omitting --expand expands everything (the default) - but only when
    there's at most one extra file. With two or more extra files you must
    say explicitly what to drill into, since "expand everything, all the way
    down, everywhere" stops being an obviously safe default once there's
    more than one level to choose from.

Usage:
    python country_to_svg.py country.json "South Africa" -o out.svg
    python country_to_svg.py country.json province.json "South Africa" -o out.svg
    python country_to_svg.py country.json province.json county.json "South Africa" -o out.svg
    python country_to_svg.py country.json province.json county.json municipality.json \
        "South Africa" --expand "Some County" -o out.svg
"""

import argparse
import json
import math
import operator
import pathlib
import re
import sys

# ---- fixed style constants - edit here, not via CLI ----
BACKGROUND_COLOR = "#77D3E7"  # everything outside the country
LAND_COLOR = "#BAEBD6"  # the country's own ground
BORDER_COLOR = "#000000"

NATIONAL_STROKE_WIDTH = 1

PROVINCE_STROKE_WIDTH = 1
PROVINCE_DASHARRAY = "2,3"
PROVINCE_DASHOFFSET = 0

EXTRA_STROKE_WIDTH = 0.5  # shared by every extra level, however deep

HEIGHT = 800  # fixed output height in px, width auto-follows country shape
PADDING = 2  # px
PRECISION = 4  # decimal places on output coordinates, not a simplification

# common attribute names across GADM / geoBoundaries / Overpass-derived exports
COUNTRY_FIELD_GUESSES = [
    "NAME_0",
    "COUNTRY",
    "ADMIN",
    "shapeGroup",
    "country",
    "admin",
    "name:en",
]
ID_FIELD_GUESSES = [
    "GID_1",
    "GID_2",
    "GID_3",
    "GID_4",
    "GID_5",
    "shapeID",
    "shapeISO",
    "osm_id",
    "id",
    "ID",
]
# ----------------------------------------------------------


def detect_field(properties, guesses):
    for g in guesses:
        if g in properties:
            return g
    return None


def name_field_guesses(n):
    """Field guesses for a feature's own name at hierarchy depth n
    (1=province, 2=first extra file, 3=second extra file, ...). GADM uses
    the same NAME_n key both as "own name" on an n-level file and as the
    "parent name" column on any deeper file, so one guess list does both jobs.
    """
    guesses = [f"NAME_{n}"]
    if n == 1:
        guesses += ["PROVINCE", "STATE"]
    guesses += [f"ADM{n}_EN", f"admin{n}Name", "shapeName"]
    return guesses


def load_country_features(path, country):
    with pathlib.Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    features = data.get("features", [])
    if not features:
        sys.exit(f"No features found in {path}")

    field = detect_field(features[0]["properties"], COUNTRY_FIELD_GUESSES)
    if not field:
        sys.exit(
            f"Could not detect a country field in {path}. Available properties: "
            + ", ".join(features[0]["properties"].keys())
            + "\nAdd the right key to COUNTRY_FIELD_GUESSES at the top of this script."
        )

    selected = [
        feat
        for feat in features
        if str(feat["properties"].get(field, "")).strip().lower() == country.strip().lower()
    ]
    if not selected:
        sample = sorted({str(f["properties"].get(field)) for f in features})[:10]
        sys.exit(f"No features matched country={country!r} in {path}. Example values: {sample}")
    return selected


def ring_area(ring):
    # shoelace formula on raw lon/lat - not a true geographic area, just a
    # consistent relative measure for comparing parts of the same geometry
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


def geometry_bounds(geometry):
    coords = list(flatten_coords(geometry))
    if not coords:
        return None

    lons = [pt[0] for pt in coords]
    lats = [pt[1] for pt in coords]

    return min(lons), min(lats), max(lons), max(lats)


def polygon_bounds(poly):
    pts = [pt for ring in poly for pt in ring]
    lons = [pt[0] for pt in pts]
    lats = [pt[1] for pt in pts]

    return min(lons), min(lats), max(lons), max(lats)


def polygon_bounds(poly):
    pts = [pt for ring in poly for pt in ring]

    lons = [pt[0] for pt in pts]
    lats = [pt[1] for pt in pts]

    return (
        min(lons),
        min(lats),
        max(lons),
        max(lats),
    )


def get_geometry_frame(geometry):
    coords = list(flatten_coords(geometry))

    if not coords:
        return None

    lons = [pt[0] for pt in coords]
    lats = [pt[1] for pt in coords]

    return (
        min(lons),
        min(lats),
        max(lons),
        max(lats),
    )


def get_national_frame(features):
    coords = [pt for feat in features for pt in flatten_coords(feat["geometry"])]

    lons = [pt[0] for pt in coords]
    lats = [pt[1] for pt in coords]

    return (
        min(lons),
        min(lats),
        max(lons),
        max(lats),
    )


def polygon_touches_frame(poly, frame, epsilon=1e-9):
    """Returns True if a polygon's bounding box touches any edge of the
    current national bounding box.
    """
    min_lon, min_lat, max_lon, max_lat = frame

    p_min_lon, p_min_lat, p_max_lon, p_max_lat = polygon_bounds(poly)

    return (
        abs(p_min_lon - min_lon) <= epsilon
        or abs(p_min_lat - min_lat) <= epsilon
        or abs(p_max_lon - max_lon) <= epsilon
        or abs(p_max_lat - max_lat) <= epsilon
    )


def prune_small_edge_parts(national_feats, province_feats, extra_feats_list, min_area):
    """Iteratively removes disconnected national polygon parts that are
    smaller than min_area and touch the current outer bounding frame.

    The frame is recomputed after every removal round, so removing one
    outer island can expose another one behind it.
    """
    changed = True
    total_removed = 0

    while changed:
        changed = False

        frame = get_national_frame(national_feats)

        for feat in national_feats + province_feats + [f for lst in extra_feats_list for f in lst]:
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

            # Never remove the entire feature.
            if not kept:
                largest = max(parts, key=operator.itemgetter(0))
                kept = [largest[1]]

            if len(kept) == 1:
                feat["geometry"] = {
                    "type": "Polygon",
                    "coordinates": kept[0],
                }
            else:
                feat["geometry"] = {
                    "type": "MultiPolygon",
                    "coordinates": kept,
                }

    return total_removed


def flatten_coords(geometry):
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    polygons = [coords] if gtype == "Polygon" else coords if gtype == "MultiPolygon" else []
    for poly in polygons:
        for ring in poly:
            yield from ring


def project_point(lon, lat, lon0, cos_lat0):
    x = (lon - lon0) * cos_lat0
    y = -lat
    return x, y


def ring_to_path_d(ring, lon0, cos_lat0, scale, off_x, off_y):
    pts = []
    for lon, lat in ring:
        x, y = project_point(lon, lat, lon0, cos_lat0)
        x = x * scale + off_x
        y = y * scale + off_y
        pts.append(f"{x:.{PRECISION}f},{y:.{PRECISION}f}")
    return "M" + " ".join(pts) + "Z"


def geometry_to_path_d(geometry, lon0, cos_lat0, scale, off_x, off_y):
    gtype = geometry["type"]
    coords = geometry["coordinates"]
    polygons = [coords] if gtype == "Polygon" else coords
    parts = []
    for poly in polygons:
        parts.extend(ring_to_path_d(ring, lon0, cos_lat0, scale, off_x, off_y) for ring in poly)
    return " ".join(parts)


def style_attrs(level) -> str:
    if level == "national":
        return (
            f'style="stroke: {BORDER_COLOR};'
            f"opacity: 0.75;"
            f"stroke-width: 1;"
            f"fill: none;"
            f'pointer-events: none;"'
        )
    if level == "province":
        return (
            f'style="stroke: {BORDER_COLOR};'
            f"opacity: 0.25;"
            f"stroke-dashoffset: {PROVINCE_DASHOFFSET};"
            f"stroke-width: {PROVINCE_STROKE_WIDTH};"
            f"stroke-dasharray: {PROVINCE_DASHARRAY};"
            f"fill: none;"
            f'pointer-events: none;"'
        )
    return f'style="fill: {LAND_COLOR};"'  # extra


def clean_id_part(text):
    text = text.strip()
    text = re.sub(r"\s+", "_", text)
    return re.sub(r"[^A-Za-z0-9_.-]", "", text)


def hierarchical_name_id(feat):
    props = feat["properties"]
    parts = []
    level = 1
    while True:
        field = f"NAME_{level}"
        if field not in props:
            break
        value = str(props[field]).strip()
        if value:
            parts.append(clean_id_part(value))
        level += 1
    return ".".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "files",
        nargs="+",
        metavar="FILE",
        help="national.json [province.json] [extra1.json extra2.json ...]",
    )
    ap.add_argument("country", help="Country name to filter on")
    ap.add_argument("-o", "--output", help="Output SVG path (default: <country>.svg)")
    ap.add_argument(
        "--expand",
        nargs="+",
        default=None,
        metavar="NAME",
        help="Names of provinces or any deeper entity to drill into. "
        "Omit to expand everything (only valid with at most one "
        "extra file). Required if 2+ extra files are given.",
    )
    ap.add_argument(
        "--min-island-percent",
        type=float,
        default=0,
        metavar="PCT",
        help="Drop disconnected polygon parts (islands, exclaves) smaller "
        "than this percent of the country's total area, from every "
        "file. Also shrinks the frame to fit what's left. 0 = keep "
        "everything (default).",
    )
    args = ap.parse_args()

    if len(args.files) < 1:
        sys.exit("Pass at least a national file.")

    national_path = args.files[0]
    province_path = args.files[1] if len(args.files) >= 2 else None
    extra_paths = args.files[2:] if len(args.files) >= 3 else []

    if len(extra_paths) >= 2 and args.expand is None:
        sys.exit(
            "Multiple extra files require --expand to be set explicitly - "
            "with more than one deeper level available, the script needs to "
            "know how far down to go for each branch."
        )
    if args.expand and not extra_paths:
        sys.exit("--expand only makes sense when at least one extra file is given.")

    national_feats = load_country_features(national_path, args.country)
    province_feats = load_country_features(province_path, args.country) if province_path else []
    extra_feats_list = [load_country_features(p, args.country) for p in extra_paths]

    if args.min_island_percent > 0:
        total_area = sum(geometry_total_area(f["geometry"]) for f in national_feats)

        min_area = total_area * (args.min_island_percent / 100.0)

        removed = prune_small_edge_parts(
            national_feats,
            province_feats,
            extra_feats_list,
            min_area,
        )

        print(
            f"Removed {removed} small outer national polygon parts "
            f"smaller than {args.min_island_percent}% of the national area.",
            file=sys.stderr,
        )

    national_coords = [pt for feat in national_feats for pt in flatten_coords(feat["geometry"])]

    national_lons = [pt[0] for pt in national_coords]
    national_lats = [pt[1] for pt in national_coords]

    lon0 = (min(national_lons) + max(national_lons)) / 2
    lat0 = (min(national_lats) + max(national_lats)) / 2
    cos_lat0 = math.cos(math.radians(lat0))

    proj_pts = [
        project_point(lon, lat, lon0, cos_lat0)
        for lon, lat in zip(national_lons, national_lats, strict=False)
    ]

    xs = [p[0] for p in proj_pts]
    ys = [p[1] for p in proj_pts]

    raw_w = max(xs) - min(xs)
    raw_h = max(ys) - min(ys)
    raw_w = max(xs) - min(xs)
    raw_h = max(ys) - min(ys)

    usable_height = HEIGHT - 2 * PADDING
    scale = usable_height / raw_h if raw_h else 1
    width = raw_w * scale + 2 * PADDING
    off_x = PADDING - min(xs) * scale
    off_y = PADDING - min(ys) * scale

    def render_group(feats, level, filled, group_id):
        if not feats:
            return ""
        id_field = detect_field(feats[0]["properties"], ID_FIELD_GUESSES)
        paths = []

        for i, feat in enumerate(feats):
            region_id = hierarchical_name_id(feat)
            if not region_id:
                region_id = str(feat["properties"].get(id_field, i)) if id_field else str(i)
            d = geometry_to_path_d(feat["geometry"], lon0, cos_lat0, scale, off_x, off_y)
            paths.append(
                f'<path id="{region_id}" {'style="vector-effect: non-scaling-stroke;"' if level in {"national", "province"} else ""} d="{d}"/>'
            )
        return f'<g id="{group_id}" {style_attrs(level)}>' + "".join(paths) + "</g>"

    layers = []

    if not province_feats:
        layers.append(render_group(national_feats, "national", filled=True, group_id="national"))
    else:
        expand_set = None if args.expand is None else {n.strip().lower() for n in args.expand}
        matched_names = set()

        levels = [province_feats] + extra_feats_list  # level_idx 0 = province, 1 = extra1, ...
        level_style = ["province"] + ["extra"] * len(extra_feats_list)

        flat = {"province": [], "extra": []}
        outline = {"province": [], "extra": []}

        def own_name(feat, level_idx):
            field = detect_field(feat["properties"], name_field_guesses(level_idx + 1))
            return str(feat["properties"].get(field, "")).strip() if field else ""

        def children_of(feat, level_idx):
            if level_idx + 1 >= len(levels) or not levels[level_idx + 1]:
                return None
            child_field = detect_field(
                levels[level_idx + 1][0]["properties"], name_field_guesses(level_idx + 1)
            )
            if not child_field:
                return None
            name = own_name(feat, level_idx)
            return [
                f
                for f in levels[level_idx + 1]
                if str(f["properties"].get(child_field, "")).strip().lower() == name.lower()
            ]

        # Pass 1: a name can match at ANY level (province or any extra file),
        # not just the shallowest one. Find every match, then walk back up
        # its ancestor chain (reading the NAME_j columns already present on
        # the matched feature itself) to mark exactly which ancestors need
        # opening to reach it - and nothing else.
        must_open = [set() for _ in levels]
        matched_names = set()

        if expand_set is None:
            for feat in province_feats:
                must_open[0].add(id(feat))
        else:
            for level_idx, feats_at_level in enumerate(levels):
                for feat in feats_at_level:
                    name = own_name(feat, level_idx)
                    if name.lower() not in expand_set:
                        continue
                    matched_names.add(name.lower())
                    must_open[level_idx].add(id(feat))
                    for j in range(level_idx):
                        field = detect_field(feat["properties"], name_field_guesses(j + 1))
                        ancestor_name = (
                            str(feat["properties"].get(field, "")).strip() if field else ""
                        )
                        for cand in levels[j]:
                            if own_name(cand, j).lower() == ancestor_name.lower():
                                must_open[j].add(id(cand))
                                break

        # Pass 2: walk top-down, drilling exactly the features marked above.
        candidates = province_feats
        level_idx = 0
        while candidates:
            next_candidates = []
            style = level_style[level_idx] if level_idx < len(level_style) else "extra"

            for feat in candidates:
                name = own_name(feat, level_idx)
                drill_requested = id(feat) in must_open[level_idx]

                if drill_requested:
                    kids = children_of(feat, level_idx)
                    if kids is not None and len(kids) >= 2:
                        outline[style].append(feat)
                        next_candidates.extend(kids)
                        continue
                    if kids is None:
                        print(
                            f"Warning: '{name}' was named but there's no deeper file to "
                            f"expand into - drawing it as-is.",
                            file=sys.stderr,
                        )
                    elif len(kids) == 1:
                        print(
                            f"Note: '{name}' has only one deeper shape (nothing real to "
                            f"subdivide into) - drawing it as-is.",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"Warning: '{name}' was named but has no matching deeper "
                            f"shapes - drawing it as-is.",
                            file=sys.stderr,
                        )

                flat[style].append(feat)

            candidates = next_candidates
            level_idx += 1

        if expand_set is not None:
            unmatched = expand_set - matched_names
            if unmatched:
                print(
                    f"Warning: --expand names with no match anywhere: {sorted(unmatched)}",
                    file=sys.stderr,
                )

        layers.extend(
            (
                render_group(flat["province"], "province", filled=True, group_id="province"),
                render_group(flat["extra"], "extra", filled=True, group_id="extra"),
                render_group(outline["extra"], "extra", filled=False, group_id="extra-outline"),
                render_group(
                    outline["province"], "province", filled=False, group_id="province-outline"
                ),
                render_group(national_feats, "national", filled=False, group_id="national"),
            )
        )

    w = round(width, PRECISION)
    h = round(HEIGHT, PRECISION)

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
        f'<rect id="background" width="{w}" height="{h}" fill="{BACKGROUND_COLOR}"/>'
        + "".join(layers)
        + "</svg>"
    )

    out_path = args.output or f"{args.country.replace(' ', '_')}.svg"
    pathlib.Path(out_path).write_text(svg, encoding="utf-8")

    print(f"Wrote {out_path}  ({w:.0f}x{h:.0f}px, {len(svg) / 1024:.1f} KB)")


if __name__ == "__main__":
    main()
