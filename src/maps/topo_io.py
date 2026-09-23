"""Loading TopoJSON/GeoJSON input files and naming/identifying features.

This module owns everything about turning files on disk into plain-Python
feature dicts: TopoJSON arc decoding and ring stitching, plain GeoJSON
loading for overlays (ecoregions/glaciers/lakes/roads), and resolving a
human-readable name (and a stable slug/id) for a feature from whatever
property field the source dataset happens to use. None of this needs
shapely -- it is purely about parsing and identifying input data, as
opposed to geometry.py, which analyzes/measures that data once loaded.
"""

import csv
import difflib
import io
import json
import pathlib
import re
import sys

from constants import NAME_FIELD_GUESSES, POINT_NAME_FIELD_GUESSES
from unidecode import unidecode

# --group Locality fuzzy-matching knobs. Province and Department are
# ALWAYS matched exactly -- fuzziness only ever applies to Locality, and
# only among localities that already share the exact same (Province,
# Department) pair. See filter_points_by_group().
_FUZZY_MATCH_THRESHOLD = 0.88


def load_topology(path):
    with pathlib.Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    if data.get("type") != "Topology":
        sys.exit(
            f'Error: {path} no es un TopoJSON válido (se esperaba "type": "Topology", '
            f"se obtuvo {data.get('type')!r})."
        )
    if "arcs" not in data or "objects" not in data:
        sys.exit(f"Error: {path} no tiene 'arcs' u 'objects', no es un TopoJSON válido.")

    return data


def decode_arcs(topology):
    """Decode every arc in the topology to a list of [x, y] absolute
    coordinate pairs, applying quantization transform + delta decoding if
    present. Returns a list of arcs (list of list of [x, y]).
    """
    raw_arcs = topology.get("arcs", [])
    transform = topology.get("transform")

    decoded = []
    for raw_arc in raw_arcs:
        points = []
        x, y = 0, 0
        for dx, dy, *_rest in raw_arc:
            x += dx
            y += dy
            points.append([x, y])
        if transform:
            scale_x, scale_y = transform["scale"]
            trans_x, trans_y = transform["translate"]
            points = [[px * scale_x + trans_x, py * scale_y + trans_y] for px, py in points]
        decoded.append(points)
    return decoded


def arcs_to_ring(arc_indices, decoded_arcs):
    """Stitch a list of TopoJSON arc indices (negative = reversed arc) into
    a single closed ring of [x, y] points.
    """
    ring = []
    for idx in arc_indices:
        arc = list(reversed(decoded_arcs[~idx])) if idx < 0 else decoded_arcs[idx]
        if ring and ring[-1] == arc[0]:
            ring.extend(arc[1:])
        else:
            ring.extend(arc)
    return ring


def topo_geometry_to_geojson_geometry(geom, decoded_arcs):
    """Convert a single TopoJSON geometry object (Polygon/MultiPolygon/
    GeometryCollection) into a GeoJSON-shaped geometry dict with literal
    coordinates (no more arc references).
    """
    gtype = geom.get("type")

    if gtype == "Polygon":
        rings = [arcs_to_ring(ring, decoded_arcs) for ring in geom["arcs"]]
        return {"type": "Polygon", "coordinates": rings}

    if gtype == "MultiPolygon":
        polys = [[arcs_to_ring(ring, decoded_arcs) for ring in poly] for poly in geom["arcs"]]
        return {"type": "MultiPolygon", "coordinates": polys}

    if gtype == "GeometryCollection":
        return {
            "type": "GeometryCollection",
            "geometries": [
                topo_geometry_to_geojson_geometry(g, decoded_arcs)
                for g in geom.get("geometries", [])
            ],
        }

    sys.exit(
        f"Error: tipo de geometría TopoJSON no soportado: {gtype!r} (se esperaba Polygon/MultiPolygon)."
    )


def load_features(path):
    """Load a TopoJSON file and return a flat list of GeoJSON-shaped
    Feature dicts (properties + literal-coordinate geometry), decoding all
    arcs. Every object under topology["objects"] is flattened into the
    result; if an object is itself a GeometryCollection its children become
    individual features.
    """
    topology = load_topology(path)
    decoded_arcs = decode_arcs(topology)

    objects = topology.get("objects", {})
    if not objects:
        sys.exit(f"Error: {path} no tiene ningún objeto en 'objects'.")

    features = []
    for obj in objects.values():
        if obj.get("type") == "GeometryCollection":
            for geom in obj.get("geometries", []):
                if geom.get("type") not in {"Polygon", "MultiPolygon"}:
                    continue
                features.append(
                    {
                        "type": "Feature",
                        "properties": dict(geom.get("properties", {})),
                        "geometry": topo_geometry_to_geojson_geometry(geom, decoded_arcs),
                    }
                )
        elif obj.get("type") in {"Polygon", "MultiPolygon"}:
            features.append(
                {
                    "type": "Feature",
                    "properties": dict(obj.get("properties", {})),
                    "geometry": topo_geometry_to_geojson_geometry(obj, decoded_arcs),
                }
            )
        # Other top-level object types (Point, LineString, ...) are not
        # relevant to administrative-boundary polygons and are skipped.

    if not features:
        sys.exit(f"No se encontraron polígonos en {path}")

    return features


def load_geojson_features(path):
    """Load a plain GeoJSON file (FeatureCollection, Feature, or a bare
    geometry) and return a flat list of GeoJSON-shaped Feature dicts
    (properties + geometry), keeping only Polygon/MultiPolygon geometries.

    Unlike load_features(), this expects plain GeoJSON with literal
    coordinates -- there is no TopoJSON arc topology to decode.
    """
    with pathlib.Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    gtype = data.get("type")
    if gtype == "FeatureCollection":
        raw_features = data.get("features", [])
    elif gtype == "Feature":
        raw_features = [data]
    elif gtype in {"Polygon", "MultiPolygon"}:
        raw_features = [{"type": "Feature", "properties": {}, "geometry": data}]
    else:
        sys.exit(
            f"Error: {path} no es un GeoJSON válido (se esperaba FeatureCollection/Feature/"
            f"Polygon/MultiPolygon, se obtuvo {gtype!r})."
        )

    features = []
    for feat in raw_features:
        geom = feat.get("geometry")
        if geom is None or geom.get("type") not in {"Polygon", "MultiPolygon"}:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": dict(feat.get("properties", {})),
                "geometry": geom,
            }
        )

    if not features:
        sys.exit(f"No se encontraron polígonos en {path}")

    return features


def load_geojson_line_features(path):
    """Load a plain GeoJSON file (FeatureCollection, Feature, or a bare
    geometry) and return a flat list of GeoJSON-shaped Feature dicts
    (properties + geometry), keeping only LineString/MultiLineString
    geometries. Used for line data (e.g. roads) as opposed to
    load_geojson_features(), which keeps only polygons.
    """
    with pathlib.Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    gtype = data.get("type")
    if gtype == "FeatureCollection":
        raw_features = data.get("features", [])
    elif gtype == "Feature":
        raw_features = [data]
    elif gtype in {"LineString", "MultiLineString"}:
        raw_features = [{"type": "Feature", "properties": {}, "geometry": data}]
    else:
        sys.exit(
            f"Error: {path} no es un GeoJSON válido (se esperaba FeatureCollection/Feature/"
            f"LineString/MultiLineString, se obtuvo {gtype!r})."
        )

    features = []
    for feat in raw_features:
        geom = feat.get("geometry")
        if geom is None or geom.get("type") not in {"LineString", "MultiLineString"}:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": dict(feat.get("properties", {})),
                "geometry": geom,
            }
        )

    if not features:
        sys.exit(f"No se encontraron líneas en {path}")

    return features


def load_raw_arc_features(path):
    """Load a TopoJSON file and return (features_with_arc_indices,
    decoded_arcs), where each feature's "arc_rings" is its polygon rings
    as raw TopoJSON arc-index lists (not yet stitched into coordinates).

    This is the topology-native view: two features that both reference the
    same arc index (regardless of sign/direction) share that arc as a
    literal boundary segment. That is the ground truth for which polygons
    are adjacent and along which edge -- no geometric intersection needed.

    Order of returned features matches the order load_features() would
    produce for the same file (same objects-dict iteration, same
    GeometryCollection flattening), so a raw-arc feature at index i
    corresponds to the load_features() feature at index i.
    """
    topology = load_topology(path)
    decoded_arcs = decode_arcs(topology)

    objects = topology.get("objects", {})
    if not objects:
        sys.exit(f"Error: {path} no tiene ningún objeto en 'objects'.")

    features = []

    def add_feature(props, arc_rings_per_poly, gtype) -> None:
        # Normalize to a flat list of rings (list of arc-index lists),
        # regardless of whether this was a Polygon or MultiPolygon.
        if gtype == "Polygon":
            rings = list(arc_rings_per_poly)
        elif gtype == "MultiPolygon":
            rings = [ring for poly in arc_rings_per_poly for ring in poly]
        else:
            return
        features.append({"properties": dict(props), "arc_rings": rings})

    for obj in objects.values():
        if obj.get("type") == "GeometryCollection":
            for geom in obj.get("geometries", []):
                if geom.get("type") not in {"Polygon", "MultiPolygon"}:
                    continue
                add_feature(geom.get("properties", {}), geom["arcs"], geom["type"])
        elif obj.get("type") in {"Polygon", "MultiPolygon"}:
            add_feature(obj.get("properties", {}), obj["arcs"], obj["type"])

    if not features:
        sys.exit(f"No se encontraron polígonos en {path}")

    return features, decoded_arcs


def load_geojson_point_features(path):
    """Load a plain GeoJSON file (FeatureCollection, Feature, or a bare
    geometry) and return a flat list of GeoJSON-shaped Feature dicts
    (properties + geometry), keeping only Point/MultiPoint geometries.
    Sibling of load_geojson_features() (polygons) and
    load_geojson_line_features() (lines).
    """
    with pathlib.Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)

    gtype = data.get("type")
    if gtype == "FeatureCollection":
        raw_features = data.get("features", [])
    elif gtype == "Feature":
        raw_features = [data]
    elif gtype in {"Point", "MultiPoint"}:
        raw_features = [{"type": "Feature", "properties": {}, "geometry": data}]
    else:
        sys.exit(
            f"Error: {path} no es un GeoJSON válido (se esperaba FeatureCollection/Feature/"
            f"Point/MultiPoint, se obtuvo {gtype!r})."
        )

    features = []
    for feat in raw_features:
        geom = feat.get("geometry")
        if geom is None or geom.get("type") not in {"Point", "MultiPoint"}:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": dict(feat.get("properties", {})),
                "geometry": geom,
            }
        )

    if not features:
        sys.exit(f"No se encontraron puntos en {path}")

    return features


def resolve_name_field(props, level=None):
    """Resolve a feature's display name for a known admin level.

    Preference order: the geoBoundaries-style universal field 'shapeName',
    then the GADM-style level-specific field 'NAME_{level}' (when the
    level is known), then 'NAME_1' as a last-resort fallback for files
    that don't carry a per-level NAME_N field at all, then the generic
    'NAME' / 'name' fields.
    """
    if props.get("shapeName"):
        return str(props["shapeName"]).strip()
    if level is not None:
        key = f"NAME_{level}"
        if props.get(key):
            return str(props[key]).strip()
    if props.get("NAME_1"):
        return str(props["NAME_1"]).strip()
    for g in ("NAME", "name"):
        if props.get(g):
            return str(props[g]).strip()
    return None


def attach_level_names(feats, level):
    """Precompute and cache each feature's resolved display name for a
    known admin level, so get_feature_name() doesn't need the level
    threaded through every call site.
    """
    for feat in feats:
        feat["properties"]["_resolved_name"] = resolve_name_field(feat["properties"], level)
    return feats


def get_feature_name(feat):
    props = feat.get("properties", {})
    if "_resolved_name" in props:
        return props["_resolved_name"]
    for g in NAME_FIELD_GUESSES:
        if props.get(g):
            return str(props[g]).strip()
    return None


def resolve_point_name(feat):
    """Like get_feature_name(), but for point features: tries the finest
    NAME_X field first (see POINT_NAME_FIELD_GUESSES). Point datasets
    often carry the point's whole containing admin hierarchy as columns
    (NAME_1..NAME_4) alongside its own name, and get_feature_name()'s
    coarsest-first order would pick the province instead of the point's
    own name -- this exists so callers naming points explicitly ask for
    the point-appropriate order instead.
    """
    props = feat.get("properties", {})
    for g in POINT_NAME_FIELD_GUESSES:
        if props.get(g):
            return str(props[g]).strip()
    return None


def point_full_chain(feat, admin_name_chain):
    """A point's full identity chain: the name chain of the admin polygon
    it was matched into (see geometry.match_points_to_polygons), e.g.
    [Province, Department], with the point's own name appended, e.g.
    [Province, Department, Locality]. This is the single definition of
    "what identifies a point" -- both the SVG id (render_points_group)
    and the --group CSV filter (filter_points_by_group) build their key
    from this, so the two can never disagree about which point is which.
    """
    return [*admin_name_chain, resolve_point_name(feat)]


def _read_text_auto(path):
    """Read a text file, detecting UTF-16 (BOM) and UTF-8 with/without BOM."""
    raw = pathlib.Path(path).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    return raw.decode("utf-8-sig")


def load_group_csv(path):
    """Load the --group CSV: GroupName,Level1,...,LevelN.
    Returns rows as {"GroupName": str, "levels": tuple[str, ...]}.
    Empty trailing level cells are allowed: a row with fewer levels is a
    shallower row. Blank lines are skipped. Accepts UTF-8 (with or without
    BOM) and UTF-16, comma or tab delimited.
    """
    text = _read_text_auto(path)
    header = text.splitlines()[0] if text.strip() else ""
    delimiter = "\t" if "\t" in header and "," not in header else ","

    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=delimiter)
    fields = reader.fieldnames or []
    level_cols = sorted(
        (c for c in fields if re.fullmatch(r"Level\d+", c)), key=lambda c: int(c[5:])
    )
    if "GroupName" not in fields or not level_cols:
        sys.exit(
            f"Error: {path} necesita las columnas GroupName,Level1,...,LevelN "
            f"(columnas encontradas: {fields})."
        )
    if [int(c[5:]) for c in level_cols] != list(range(1, len(level_cols) + 1)):
        sys.exit(f"Error: las columnas Level de {path} deben ser consecutivas desde Level1.")

    rows = []
    for line_no, row in enumerate(reader, start=2):
        name = (row.get("GroupName") or "").strip()
        cells = [(row.get(c) or "").strip() for c in level_cols]

        if not name and not any(cells):
            continue  # blank line

        depth = len(cells)
        while depth and not cells[depth - 1]:
            depth -= 1
        levels = tuple(cells[:depth])

        if not name:
            sys.exit(f"Error: {path} línea {line_no}: hay niveles pero falta GroupName.")
        if not levels:
            sys.exit(f"Error: {path} línea {line_no}: {name!r} no tiene ningún nivel.")
        if not all(levels):
            sys.exit(f"Error: {path} línea {line_no}: hay un nivel vacío entre niveles con valor.")

        rows.append({"GroupName": name, "levels": levels})

    if not rows:
        sys.exit(f"Error: {path} no tiene ninguna fila.")
    return rows


def group_depth(rows):
    """Deepest non-empty level across all rows (the CSV's effective N)."""
    return max(len(r["levels"]) for r in rows)


def point_group_key(feat, n_levels):
    """(NAME_1, ..., NAME_{N-1}, own name) -- same identity the old
    Province/Department/Locality key used, generalized to N levels.
    """
    props = feat.get("properties", {})
    parents = tuple(str(props.get(f"NAME_{i}", "")).strip() for i in range(1, n_levels))
    return (*parents, resolve_point_name(feat))


def filter_points_by_group(point_feats, rows, strict=False):
    """Keep only points listed in the --group CSV. All levels but the last
    match exactly; the last level is fuzzy-matched among points sharing
    the same parent prefix. Matched points get properties["_matched_group"].
    Rows shallower than the CSV's full depth are skipped, since they can't
    identify a single point. strict=False silently skips rows with no
    matching point.
    """
    n = group_depth(rows)
    feats_by_key = {}
    names_by_parent = {}
    for feat in point_feats:
        key = point_group_key(feat, n)
        if not all(key):
            continue
        feats_by_key.setdefault(key, []).append(feat)
        names_by_parent.setdefault(key[:-1], set()).add(key[-1])

    kept = []
    for row in rows:
        if len(row["levels"]) != n:
            continue  # shallower rows can't identify a single point
        key = row["levels"]
        matches = feats_by_key.get(key)
        if not matches:
            fuzzy = _best_fuzzy_locality(key[-1], names_by_parent.get(key[:-1], set()))
            if fuzzy is not None:
                matches = feats_by_key.get((*key[:-1], fuzzy[0]))
        if not matches:
            if strict:
                return None, {
                    "row": row,
                    "available_localities": sorted(names_by_parent.get(key[:-1], [])),
                }
            continue
        for feat in matches:
            feat["properties"]["_matched_group"] = row["GroupName"]
        kept.extend(matches)
    return kept, None


def _normalize_for_fuzzy(s):
    """Accent/case-insensitive, whitespace-trimmed form of a string, used
    as the base string for both fuzzy-matching passes below.
    """
    return unidecode(str(s)).strip().lower()


def _best_fuzzy_locality(target_locality, candidate_localities, threshold=_FUZZY_MATCH_THRESHOLD):
    """Fuzzy-match a single Locality name against a pool of candidates
    that the caller has ALREADY restricted to the same exact (Province,
    Department) pair -- Province and Department are never fuzzy, only
    Locality, and only among localities that share the same admin parent.

    Two passes, tried in order, each requiring >= threshold:
      1. Plain normalized-string similarity (difflib ratio).
      2. Token-sorted similarity, as a fallback when word order differs.

    Returns (matched_locality, score, pass_name) for the best candidate
    that clears the threshold on whichever pass finds one, or None if
    neither pass finds a match good enough.
    """
    if not candidate_localities:
        return None

    target_norm = _normalize_for_fuzzy(target_locality)
    best_name, best_score = None, 0.0
    for cand in candidate_localities:
        score = difflib.SequenceMatcher(None, target_norm, _normalize_for_fuzzy(cand)).ratio()
        if score > best_score:
            best_name, best_score = cand, score
    if best_name is not None and best_score >= threshold:
        return best_name, best_score, "fuzzy"

    return None


def slugify(name):
    val = unidecode(name)
    val = re.sub(r"\s+", "_", val)
    return re.sub(r"[^A-Za-z0-9_.-]", "", val)


def hierarchical_name_id(names, fallback):
    parts = [slugify(n) for n in names if n]
    return ".".join(parts) if parts else fallback


def classify_arcs_by_group(raw_feats, group_names, source_label="entidades"):
    """Pair up every TopoJSON arc that sits on the boundary between two
    named groups, from raw arc-index topology alone (see
    load_raw_arc_features). Purely a topology lookup -- no geometry
    library involved -- so it lives next to the raw-arc loader rather than
    in geometry.py.
    """
    owners = {}
    for feat, group_name in zip(raw_feats, group_names, strict=False):
        if group_name is None:
            continue
        seen_in_this_feature = set()
        for ring in feat["arc_rings"]:
            for signed_idx in ring:
                abs_idx = signed_idx if signed_idx >= 0 else ~signed_idx
                seen_in_this_feature.add(abs_idx)
        for abs_idx in seen_in_this_feature:
            owners.setdefault(abs_idx, set()).add(group_name)

    pair_arcs = {}
    bad_arcs = []
    for abs_idx, names in owners.items():
        if len(names) == 2:
            pair_arcs.setdefault(frozenset(names), []).append(abs_idx)
        elif len(names) > 2:
            bad_arcs.append((abs_idx, sorted(names)))

    if bad_arcs:
        details = "; ".join(f"arc {i} owned by {names}" for i, names in bad_arcs[:10])
        sys.exit(
            f"Error: {len(bad_arcs)} arco(s) de {source_label} están referenciados por más de "
            f"2 grupos, lo cual no es una topología plana válida: {details}"
        )

    return pair_arcs
