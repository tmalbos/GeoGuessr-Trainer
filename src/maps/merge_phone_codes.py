#!/usr/bin/env python3

import argparse
import re
from collections import defaultdict

import pandas as pd
from lxml import etree
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from svgpathtools import parse_path

SVG_NS = "http://www.w3.org/2000/svg"
NS = {"svg": SVG_NS}


# ============================================================================
# NAME NORMALIZATION
# ============================================================================


def normalize_name(value):
    """Normalize a name for matching.

    Non-ASCII characters are deleted.

    Examples:
        Tést       -> tst
        New Town   -> newtown
        São Paulo  -> sop aulo -> sopaulo
        CamelCase  -> camelcase

    """
    if value is None:
        return ""

    value = str(value).strip().lower()

    # Delete non-ASCII characters.
    value = value.encode("ascii", "ignore").decode("ascii")

    # Remove everything except ASCII letters and digits.
    return re.sub(r"[^a-z0-9]", "", value)


# ============================================================================
# CSV
# ============================================================================

CSV_LEVELS = [
    "level_1",
    "level_2",
]


def load_csv(csv_path):

    df = pd.read_csv(csv_path, dtype=str).fillna("")

    required = {
        "cluster",
        "level_1",
        "level_2",
    }

    missing = required - set(df.columns)

    if missing:
        msg = f"CSV is missing columns: {sorted(missing)}"
        raise ValueError(msg)

    records = []

    for _, row in df.iterrows():
        record = {
            "cluster": row["cluster"].strip(),
            "level_1": row["level_1"].strip(),
            "level_2": row["level_2"].strip(),
        }

        record["_normalized"] = {level: normalize_name(record[level]) for level in CSV_LEVELS}

        records.append(record)

    return records


# ============================================================================
# MATCHING
# ============================================================================


def parse_svg_name(svg_id):

    if not svg_id:
        return []

    return [part for part in svg_id.split(".") if part]


def match_svg_id(svg_id, csv_records):

    svg_parts = parse_svg_name(svg_id)

    if not svg_parts:
        return None, "invalid_id"

    svg_parts = [normalize_name(part) for part in svg_parts]

    # The rightmost SVG component is the most specific one.
    #
    # Start with the most specific component and progressively add parent
    # components when necessary.
    #
    # Example:
    #
    #   level_1.level_2
    #
    # Attempts:
    #
    #   level_2
    #   level_1 + level_2

    max_levels = min(len(svg_parts), len(CSV_LEVELS))

    for level_count in range(1, max_levels + 1):
        svg_suffix = svg_parts[-level_count:]

        csv_suffix = CSV_LEVELS[-level_count:]

        candidates = []

        for record in csv_records:
            matches = True

            for svg_value, csv_level in zip(svg_suffix, csv_suffix, strict=False):
                if record["_normalized"][csv_level] != svg_value:
                    matches = False
                    break

            if matches:
                candidates.append(record)

        if len(candidates) == 1:
            return (candidates[0], f"{level_count}_level")

        if len(candidates) == 0:
            # No match at this level. Adding parent levels cannot create one.
            return None, "unmatched"

        # More than one match: add another administrative level.

    return None, "ambiguous"


# ============================================================================
# SVG PATH PARSING
# ============================================================================


def split_subpaths(path):
    """Split a parsed SVG path into independent subpaths.

    A new subpath begins whenever the next segment does not start where the
    previous segment ended.

    This is important because a single SVG <path> can contain:

        M...Z M...Z M...Z

    which represents multiple independent pieces of geometry.
    """
    subpaths = []

    current = []

    previous_end = None

    for segment in path:
        if current and previous_end is not None and abs(segment.start - previous_end) > 1e-9:
            subpaths.append(current)

            current = []

        current.append(segment)

        previous_end = segment.end

    if current:
        subpaths.append(current)

    return subpaths


def sample_subpath(subpath):

    points = []

    for segment in subpath:
        length = segment.length(error=1e-5)

        # Sampling density is deliberately high enough for the union
        # operation, but only used on paths that are actually being merged.
        samples = max(2, min(1000, int(length / 0.5) + 2))

        for i in range(samples):
            t = i / (samples - 1)

            point = segment.point(t)

            points.append((point.real, point.imag))

    if len(points) < 3:
        return None

    # Ensure closure.
    if points[0] != points[-1]:
        points.append(points[0])

    return points


def svg_path_to_geometry(path_element):
    """Convert a path to geometry for unioning.

    This function is ONLY called for paths that are part of a phone-code group
    requiring a union.

    Multiple subpaths are kept separate.
    """
    path_data = path_element.get("d")

    if not path_data:
        return None

    parsed = parse_path(path_data)

    subpaths = split_subpaths(parsed)

    polygons = []

    for subpath in subpaths:
        points = sample_subpath(subpath)

        if not points:
            continue

        polygon = Polygon(points)

        if polygon.is_empty:
            continue

        if not polygon.is_valid:
            polygon = polygon.buffer(0)

        if not polygon.is_empty:
            polygons.append(polygon)

    if not polygons:
        return None

    return unary_union(polygons)


# ============================================================================
# GEOMETRY → SVG
# ============================================================================


def ring_to_svg(ring):

    coords = list(ring.coords)

    if not coords:
        return ""

    commands = [f"M{coords[0][0]:.6f},{coords[0][1]:.6f}"]

    for x, y in coords[1:]:
        commands.append(f"L{x:.6f},{y:.6f}")

    commands.append("Z")

    return " ".join(commands)


def geometry_to_svg_path(geometry):

    if geometry.is_empty:
        return ""

    if isinstance(geometry, Polygon):
        polygons = [geometry]

    elif isinstance(geometry, MultiPolygon):
        polygons = list(geometry.geoms)

    else:
        polygons = [geom for geom in geometry.geoms if isinstance(geom, Polygon)]

    parts = []

    for polygon in polygons:
        parts.append(ring_to_svg(polygon.exterior))

        parts.extend(ring_to_svg(interior) for interior in polygon.interiors)

    return " ".join(parts)


# ============================================================================
# SVG STRUCTURE
# ============================================================================


def find_extra(root):

    element = root.xpath(".//*[@id='Land']")

    if element:
        return element[0]

    msg = 'Could not find an SVG element with id="Land".'
    raise ValueError(msg)


def copy_visual_attributes(source, target) -> None:
    """Copy visual attributes from the first source path.

    No geometry-related attributes are copied because the new path's d
    represents the union geometry.
    """
    for attribute in (
        "style",
        "fill",
        "stroke",
        "stroke-width",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-miterlimit",
        "stroke-dasharray",
        "stroke-dashoffset",
        "fill-rule",
        "clip-rule",
        "opacity",
        "fill-opacity",
        "stroke-opacity",
        "vector-effect",
    ):
        value = source.get(attribute)

        if value is not None:
            target.set(attribute, value)


# ============================================================================
# SORTING
# ============================================================================


def phone_code_sort_key(code):
    """Lexicographic ordering.

    Therefore:

        010
        0111
        020

    NOT numeric ordering:

        010
        020
        0111
    """
    return code


# ============================================================================
# PROCESSING
# ============================================================================


def process(csv_path, svg_path, output_path) -> None:

    print("Loading CSV...")

    csv_records = load_csv(csv_path)

    print(f"Loaded {len(csv_records):,} geographic mappings.")

    print("Loading SVG...")

    parser = etree.XMLParser(remove_blank_text=False)

    tree = etree.parse(str(svg_path), parser)

    root = tree.getroot()

    extra = find_extra(root)

    paths = extra.xpath(".//svg:path", namespaces=NS)

    print(f"Found {len(paths):,} SVG paths.")

    # ------------------------------------------------------------------------
    # Match paths.
    #
    # The original SVG elements are retained in memory.
    # Nothing is converted to geometry at this point.
    # ------------------------------------------------------------------------

    matched = defaultdict(list)

    unmatched = []

    stats = defaultdict(int)

    for path in paths:
        svg_id = path.get("id")

        record, match_type = match_svg_id(svg_id, csv_records)

        if record is None:
            unmatched.append(path)

            stats[match_type] += 1

            continue

        matched[record["cluster"]].append(path)

        stats[match_type] += 1

    print()
    print("MATCHING RESULTS")
    print("----------------")

    for key in sorted(stats):
        print(f"{key}: {stats[key]:,}")

    print()
    print(f"Matched phone codes: {len(matched):,}")

    # ------------------------------------------------------------------------
    # IMPORTANT:
    #
    # A phone-code group with exactly one path is NOT converted to Shapely.
    # It is simply renamed.
    #
    # A group with multiple paths is unioned.
    # ------------------------------------------------------------------------

    replacements = []

    for phone_code, source_paths in matched.items():
        if len(source_paths) == 1:
            path = source_paths[0]

            path.set("id", phone_code)

            continue

        geometries = []

        geometry_failed = False

        for path in source_paths:
            geometry = svg_path_to_geometry(path)

            if geometry is None:
                geometry_failed = True

                break

            geometries.append(geometry)

        if geometry_failed:
            # If union cannot be safely performed, preserve the original
            # paths rather than destroying them.
            continue

        merged_geometry = unary_union(geometries)

        source = source_paths[0]

        replacement = etree.Element(f"{{{SVG_NS}}}path")

        replacement.set("id", phone_code)

        replacement.set("d", geometry_to_svg_path(merged_geometry))

        copy_visual_attributes(source, replacement)

        replacements.append((source_paths, replacement))

    # ------------------------------------------------------------------------
    # Apply only the necessary replacements.
    # ------------------------------------------------------------------------

    for source_paths, replacement in replacements:
        first = source_paths[0]

        parent = first.getparent()

        if parent is None:
            continue

        insertion_index = parent.index(first)

        for path in source_paths:
            if path.getparent() is parent:
                parent.remove(path)

        parent.insert(insertion_index, replacement)

    # ------------------------------------------------------------------------
    # Sort ONLY the resulting matched phone-code paths.
    #
    # Unmatched paths remain untouched and are not reordered.
    # ------------------------------------------------------------------------

    matched_paths = []

    for phone_code in sorted(matched.keys(), key=phone_code_sort_key):
        # Find the resulting element.
        #
        # For a single-path group, the original element has been renamed.
        # For a multi-path group, the replacement has been inserted.
        #
        # We identify it by id.
        elements = extra.xpath(f".//*[@id={phone_code!r}]")

        if elements:
            matched_paths.extend(elements)

    # Do not reorder the entire SVG or move unmatched elements.
    #
    # Only reorder the matched phone-code elements as a contiguous ordered
    # block at the end of the geographic group.
    #
    # This preserves unmatched elements themselves unchanged.
    #
    # Remove matched output elements from their current parents, then append
    # them in the requested lexicographic phone-code order.
    #
    # However, this is only safe when all matched elements share the same
    # parent. The normal expected structure is that they are direct children
    # of #extra.

    direct_matched = [element for element in matched_paths if element.getparent() is extra]

    if len(direct_matched) == len(matched_paths):
        for element in direct_matched:
            extra.remove(element)

        for element in direct_matched:
            extra.append(element)

    tree.write(str(output_path), encoding="UTF-8", xml_declaration=True, pretty_print=True)

    print()
    print("Done.")

    print(f"Output: {output_path}")


# ============================================================================
# CLI
# ============================================================================


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument("--csv", required=True)

    parser.add_argument("--svg", required=True)

    parser.add_argument("--output", required=True)

    args = parser.parse_args()

    process(csv_path=args.csv, svg_path=args.svg, output_path=args.output)


if __name__ == "__main__":
    main()
