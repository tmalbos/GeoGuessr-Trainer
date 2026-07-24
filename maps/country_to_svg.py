#!/usr/bin/env python3
"""country_to_svg.py.

Convert GeoJSON administrative boundaries into a minimal, standardized SVG
for a single country, using Shapely for hierarchical boundary conflation.

Pass files in order: national.json [province.json] [extra1.json extra2.json ...]

Usage:
    python country_to_svg.py country.json "South Africa" -o out.svg
    python country_to_svg.py country.json province.json "South Africa" -o out.svg
    python country_to_svg.py country.json province.json county.json "South Africa" -o out.svg
"""

import argparse
import json
import math
import operator
import pathlib
import re
import sys
import time

try:
    import shapely
    from shapely.geometry import (
        GeometryCollection,
        MultiPolygon,
        Polygon,
        mapping,
        shape,
    )
    from shapely.ops import snap, unary_union
except ImportError:
    sys.exit(
        "Error: La librería 'shapely' es requerida. Por favor instálala ejecutando: pip install shapely"
    )

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
PRECISION = 4  # decimal places on output coordinates

# Tolerancia en grados para alinear bordes compartidos (~4e-4 ≈ 40m)
CONFLATION_TOLERANCE = 4e-4

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


# --- Funciones Auxiliares de Geometría con Shapely ---


def fix_geom(geom):
    """Asegura que la geometría no sea None, sea válida y no esté vacía."""
    if geom is None:
        return Polygon()
    if geom.is_empty:
        return geom
    if not geom.is_valid:
        try:
            from shapely.validation import make_valid

            geom = make_valid(geom)
        except Exception:
            geom = geom.buffer(0)
    return geom if geom is not None else Polygon()


def safe_boundary(geom):
    """Obtiene el borde (.boundary) de una geometría sin riesgo de retornar None o crashear."""
    g = fix_geom(geom)
    if g.is_empty:
        return Polygon()
    try:
        b = g.boundary
        return fix_geom(b)
    except Exception:
        try:
            b = g.buffer(0).boundary
            return fix_geom(b)
        except Exception:
            return Polygon()


def extract_polygons(geom):
    """Extrae todos los polígonos de una geometría."""
    geom = fix_geom(geom)
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom] if geom.area > 0 else []
    if isinstance(geom, MultiPolygon):
        return [p for p in geom.geoms if not p.is_empty and p.area > 0]
    if isinstance(geom, GeometryCollection):
        polys = []
        for g in geom.geoms:
            polys.extend(extract_polygons(g))
        return polys
    return []


def to_multi_or_single_polygon(polygons):
    valid_polys = []
    for p in polygons:
        valid_polys.extend(extract_polygons(p))
    if not valid_polys:
        return Polygon()
    if len(valid_polys) == 1:
        return valid_polys[0]
    return MultiPolygon(valid_polys)


def clean_to_geojson_dict(geom):
    poly = to_multi_or_single_polygon(extract_polygons(geom))
    return mapping(poly)


def safe_op(geom1, geom2, op):
    """Ejecuta una operación booleana (intersection, difference, union).
    Garantiza retornar SIEMPRE un objeto Shapely (nunca None).
    """
    g1 = fix_geom(geom1)
    g2 = fix_geom(geom2)

    if g1.is_empty and op in {"intersection", "difference"}:
        return Polygon()
    if g2.is_empty:
        if op == "intersection":
            return Polygon()
        if op in {"difference", "union"}:
            return g1

    try:
        res = getattr(g1, op)(g2)
        if res is not None:
            return fix_geom(res)
    except Exception:
        pass

    # Intento secundario con buffer(0) si GEOS se queja de la topología
    try:
        res = getattr(g1.buffer(0), op)(g2.buffer(0))
        if res is not None:
            return fix_geom(res)
    except Exception:
        pass

    return Polygon()


def remove_spikes(geom, eps=1e-5):
    """Aplica Apertura Morfológica (Erosión -> Dilatación) para eliminar
    líneas finas, espinas (spikes) y puentes de ancho microscópico.
    """
    g = fix_geom(geom)
    if g.is_empty:
        return g
    try:
        # 1. Erosionar (el polígono se contrae, destruyendo hilos sin grosor)
        eroded = g.buffer(-eps)
        # 2. Dilatar (vuelve al tamaño original, pero el hilo ya no existe)
        dilated = eroded.buffer(eps)
        # 3. Simplificar mínimamente para limpiar los nodos restantes
        cleaned = dilated.simplify(eps, preserve_topology=True)
        return fix_geom(cleaned)
    except Exception:
        return g


def align_and_fill_children(
    child_geoms, parent_geom, tolerance=CONFLATION_TOLERANCE, level_name="entidades"
):
    """Ajusta geometrías, elimina espinas al final y sella los huecos resultantes exclusivamente contra el borde mayor."""
    if not child_geoms:
        return []

    parent_geom = fix_geom(parent_geom)
    if parent_geom.is_empty:
        return [fix_geom(g) for g in child_geoms]

    n = len(child_geoms)
    print(
        f"  [1/4] Ajustando y recortando {n} {level_name} al marco contenedor...", file=sys.stderr
    )

    # Paso A: Ajustar y recortar al marco del padre
    clipped_children = []
    for idx, g in enumerate(child_geoms):
        g_clean = fix_geom(g)
        if g_clean.is_empty:
            clipped_children.append(Polygon())
            continue
        g_snapped = fix_geom(snap(g_clean, parent_geom, tolerance))
        g_clipped = safe_op(g_snapped, parent_geom, "intersection")
        clipped_children.append(g_clipped)

    # Paso B: Alinear bordes de los hijos entre sí
    print(f"  [2/4] Alineando bordes adyacentes entre {level_name}...", file=sys.stderr)
    for i in range(n):
        if clipped_children[i].is_empty:
            continue
        for j in range(n):
            if i != j and not clipped_children[j].is_empty:
                clipped_children[i] = fix_geom(
                    snap(clipped_children[i], clipped_children[j], tolerance)
                )

    # Paso C: Resolver superposiciones entre hijos
    print("  [3/4] Detectando y resolviendo superposiciones...", file=sys.stderr)
    for i in range(n):
        if clipped_children[i].is_empty:
            continue
        for j in range(i + 1, n):
            if clipped_children[j].is_empty:
                continue
            if clipped_children[i].intersects(clipped_children[j]):
                overlap = safe_op(clipped_children[i], clipped_children[j], "intersection")
                for ov in extract_polygons(overlap):
                    if ov.area <= 1e-10:
                        continue
                    rem_i = safe_op(clipped_children[i], ov, "difference")
                    rem_j = safe_op(clipped_children[j], ov, "difference")
                    b_ov = safe_boundary(ov)
                    shared_i = safe_op(b_ov, safe_boundary(rem_i), "intersection")
                    len_i = (
                        shared_i.length if shared_i is not None and not shared_i.is_empty else 0.0
                    )
                    shared_j = safe_op(b_ov, safe_boundary(rem_j), "intersection")
                    len_j = (
                        shared_j.length if shared_j is not None and not shared_j.is_empty else 0.0
                    )

                    if len_i >= len_j:
                        clipped_children[j] = safe_op(clipped_children[j], ov, "difference")
                    else:
                        clipped_children[i] = safe_op(clipped_children[i], ov, "difference")

    # Paso D: Resolver huecos internos generales
    non_empty = [c for c in clipped_children if not c.is_empty]
    if non_empty:
        try:
            children_union = fix_geom(unary_union(non_empty))
        except Exception:
            children_union = fix_geom(unary_union([g.buffer(0) for g in non_empty]))

        gaps = safe_op(parent_geom, children_union, "difference")
        for gap in extract_polygons(gaps):
            if gap.area <= 1e-10:
                continue
            best_idx = -1
            max_shared_len = -1.0
            gap_b = safe_boundary(gap)
            for idx in range(n):
                if clipped_children[idx].is_empty:
                    continue
                shared = safe_op(gap_b, safe_boundary(clipped_children[idx]), "intersection")
                shared_len = shared.length if shared is not None and not shared.is_empty else 0.0
                if shared_len > max_shared_len:
                    max_shared_len = shared_len
                    best_idx = idx
            if best_idx != -1:
                clipped_children[best_idx] = safe_op(clipped_children[best_idx], gap, "union")

    # =========================================================================
    # PASO E: CORTE Y REASIGNACIÓN DE HUÉRFANOS POR SOLAPAMIENTO ESPACIAL REAL
    # =========================================================================
    print("  [4/4] Cortando spikes y reasignando fragmentos huérfanos...", file=sys.stderr)

    spike_eps = 4e-4  # Tolerancia del corte (~20 metros)
    orphan_slivers = []
    cleaned_children = []

    # 1. Cortar físicamente los spikes de cada polígono mediante diferencia geométrica
    for geom in clipped_children:
        if geom.is_empty:
            cleaned_children.append(geom)
            continue

        try:
            eroded = geom.buffer(-spike_eps, join_style=2)
            if eroded.is_empty:
                cleaned_children.append(geom)
                continue
            core = eroded.buffer(spike_eps, join_style=2)
            core = safe_op(geom, core, "intersection")

            spikes = safe_op(geom, core, "difference")
            cleaned_children.append(fix_geom(core))

            orphan_slivers.extend(poly for poly in extract_polygons(spikes) if poly.area > 1e-10)
        except Exception:
            cleaned_children.append(geom)

    # 2. Agregar también los huecos vacíos del borde superior a la lista de huérfanos
    non_empty_cleaned = [c for c in cleaned_children if not c.is_empty]
    if non_empty_cleaned:
        try:
            union_cleaned = fix_geom(unary_union(non_empty_cleaned))
        except Exception:
            union_cleaned = fix_geom(unary_union([g.buffer(0) for g in non_empty_cleaned]))

        border_gaps = safe_op(parent_geom, union_cleaned, "difference")
        orphan_slivers.extend(gap for gap in extract_polygons(border_gaps) if gap.area > 1e-10)

    # 3. REASIGNACIÓN POR SOLAPAMIENTO REAL (ÁREA DE INTERSECCIÓN MÁXIMA)
    # En lugar de medir líneas lejanas, medimos qué vecino "envuelve" mejor al huérfano.
    for orphan in orphan_slivers:
        if orphan.is_empty or orphan.area <= 1e-10:
            continue

        best_idx = -1
        max_overlap_area = -1.0

        # Generamos un pequeño buffer alrededor del huérfano para capturar al vecino correcto
        orphan_buffer = orphan.buffer(spike_eps * 2, join_style=2)

        for idx in range(n):
            if cleaned_children[idx].is_empty:
                continue

            try:
                # Calculamos el área real de contacto/solapamiento con el vecino ampliado
                overlap = safe_op(orphan_buffer, cleaned_children[idx], "intersection")
                ov_area = overlap.area if overlap is not None and not overlap.is_empty else 0.0

                if ov_area > max_overlap_area:
                    max_overlap_area = ov_area
                    best_idx = idx
            except Exception:
                continue

        # Si encuentra un vecino válido por cercanía/solapamiento, se le fusiona
        if best_idx != -1:
            cleaned_children[best_idx] = safe_op(cleaned_children[best_idx], orphan, "union")

    return cleaned_children


def get_feature_name(feat, level=1):
    props = feat.get("properties", {})
    guesses = name_field_guesses(level)
    for g in guesses:
        if g in props and props[g] is not None:
            val = str(props[g]).strip()
            if val:
                return val
    return None


def match_extra_to_province(extra_feat, extra_geom, prov_feats, prov_geoms):
    """Asigna una entidad extra a su provincia correspondiente mediante nombre o traslape espacial."""
    extra_prov_name = get_feature_name(extra_feat, level=1)
    if extra_prov_name:
        target = extra_prov_name.strip().lower()
        for idx, pf in enumerate(prov_feats):
            p_name = get_feature_name(pf, level=1)
            if p_name and p_name.strip().lower() == target:
                return idx

    best_idx = -1
    max_area = -1.0
    for idx, pg in enumerate(prov_geoms):
        if pg.is_empty:
            continue
        try:
            inter_area = extra_geom.intersection(pg).area
        except Exception:
            inter_area = extra_geom.buffer(0).intersection(pg.buffer(0)).area

        if inter_area > max_area:
            max_area = inter_area
            best_idx = idx

    if best_idx != -1 and max_area > 0:
        return best_idx
    return 0


# --- Área y Proyección SVG ---


def ring_area(ring):
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


def polygon_bounds(poly):
    pts = [pt for ring in poly for pt in ring]
    lons = [pt[0] for pt in pts]
    lats = [pt[1] for pt in pts]
    return (min(lons), min(lats), max(lons), max(lats))


def get_national_frame(features):
    coords = [pt for feat in features for pt in flatten_coords(feat["geometry"])]
    lons = [pt[0] for pt in coords]
    lats = [pt[1] for pt in coords]
    return (min(lons), min(lats), max(lons), max(lats))


def polygon_touches_frame(poly, frame, epsilon=1e-9):
    min_lon, min_lat, max_lon, max_lat = frame
    p_min_lon, p_min_lat, p_max_lon, p_max_lat = polygon_bounds(poly)
    return (
        abs(p_min_lon - min_lon) <= epsilon
        or abs(p_min_lat - min_lat) <= epsilon
        or abs(p_max_lon - max_lon) <= epsilon
        or abs(p_max_lat - max_lat) <= epsilon
    )


def prune_small_edge_parts(national_feats, province_feats, extra_feats_list, min_area):
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

            if not kept:
                largest = max(parts, key=operator.itemgetter(0))
                kept = [largest[1]]

            if len(kept) == 1:
                feat["geometry"] = {"type": "Polygon", "coordinates": kept[0]}
            else:
                feat["geometry"] = {"type": "MultiPolygon", "coordinates": kept}

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
    return f'style="fill: {LAND_COLOR};"'


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
        "--min-island-percent",
        type=float,
        default=0,
        metavar="PCT",
        help="Drop disconnected polygon parts smaller than this percent.",
    )
    ap.add_argument(
        "--conflation-tolerance",
        type=float,
        default=CONFLATION_TOLERANCE,
        metavar="DEG",
        help=f"Conflation tolerance in degrees. Default {CONFLATION_TOLERANCE}.",
    )
    args = ap.parse_args()

    if len(args.files) < 1:
        sys.exit("Pass at least a national file.")

    national_path = args.files[0]
    province_path = args.files[1] if len(args.files) >= 2 else None
    extra_paths = args.files[2:] if len(args.files) >= 3 else []

    national_feats = load_country_features(national_path, args.country)
    province_feats = load_country_features(province_path, args.country) if province_path else []
    extra_feats_list = [load_country_features(p, args.country) for p in extra_paths]

    if args.min_island_percent > 0:
        total_area = sum(geometry_total_area(f["geometry"]) for f in national_feats)
        min_area = total_area * (args.min_island_percent / 100.0)
        removed = prune_small_edge_parts(national_feats, province_feats, extra_feats_list, min_area)
        print(f"Removed {removed} small outer national polygon parts.", file=sys.stderr)

    # --- INICIO DEL PROCESAMIENTO CONFLACIONAL JERÁRQUICO CON SHAPELY ---
    t0 = time.perf_counter()

    # Paso 1: Definir la frontera nacional maestra
    nat_geoms = [fix_geom(shape(f["geometry"])) for f in national_feats]
    national_union = fix_geom(unary_union(nat_geoms))

    # Paso 2: Ajustar Provincias al marco nacional
    cleaned_prov_geoms = []
    if province_feats:
        print("\n=== Procesando Provincias respecto al Borde Nacional ===", file=sys.stderr)
        raw_prov_geoms = [fix_geom(shape(f["geometry"])) for f in province_feats]
        cleaned_prov_geoms = align_and_fill_children(
            raw_prov_geoms,
            national_union,
            tolerance=args.conflation_tolerance,
            level_name="provincias",
        )
        for i, feat in enumerate(province_feats):
            feat["geometry"] = clean_to_geojson_dict(cleaned_prov_geoms[i])

    # Paso 3: Ajustar niveles Extra a sus provincias correspondientes
    for extra_idx, extra_feats in enumerate(extra_feats_list):
        print(f"\n=== Procesando Nivel Extra {extra_idx + 1} ===", file=sys.stderr)
        if province_feats and cleaned_prov_geoms:
            prov_groups = {i: [] for i in range(len(province_feats))}
            for e_idx, ef in enumerate(extra_feats):
                eg = fix_geom(shape(ef["geometry"]))
                p_idx = match_extra_to_province(ef, eg, province_feats, cleaned_prov_geoms)
                prov_groups[p_idx].append((e_idx, ef, eg))

            active_groups = [g for g in prov_groups.values() if g]
            total_groups = len(active_groups)
            processed_count = 0

            for p_idx, items in prov_groups.items():
                if not items:
                    continue
                processed_count += 1
                prov_name = (
                    get_feature_name(province_feats[p_idx], level=1) or f"Provincia #{p_idx}"
                )
                print(
                    f"\n -> [{processed_count}/{total_groups}] Ajustando sub-elementos en {prov_name}...",
                    file=sys.stderr,
                )

                p_geom = cleaned_prov_geoms[p_idx]
                e_indices, _e_feats, e_geoms = zip(*items, strict=False)
                cleaned_e_geoms = align_and_fill_children(
                    list(e_geoms),
                    p_geom,
                    tolerance=args.conflation_tolerance,
                    level_name="sub-unidades",
                )
                for idx_in_items, orig_idx in enumerate(e_indices):
                    extra_feats[orig_idx]["geometry"] = clean_to_geojson_dict(
                        cleaned_e_geoms[idx_in_items]
                    )
        else:
            raw_e_geoms = [fix_geom(shape(f["geometry"])) for f in extra_feats]
            cleaned_e_geoms = align_and_fill_children(
                raw_e_geoms,
                national_union,
                tolerance=args.conflation_tolerance,
                level_name="sub-unidades",
            )
            for i, feat in enumerate(extra_feats):
                extra_feats[i]["geometry"] = clean_to_geojson_dict(cleaned_e_geoms[i])

    print(f"\n✔ Ajuste de límites completado en {time.perf_counter() - t0:.2f}s\n", file=sys.stderr)

    # --- GENERACIÓN DEL SVG FINAL ---
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
                f'<path id="{region_id}" '
                f"{'style="vector-effect: non-scaling-stroke;"' if level in {'national', 'province'} else ''} "
                f'd="{d}"/>'
            )
        return f'<g id="{group_id}" {style_attrs(level)}>' + "".join(paths) + "</g>"

    layers = []

    if not province_feats:
        layers.append(render_group(national_feats, "national", filled=True, group_id="national"))

    flat = {
        "province": province_feats,
        "extra": [feat for extra_level in extra_feats_list for feat in extra_level],
    }

    layers.extend(
        (
            render_group(
                flat["extra"],
                "extra",
                filled=True,
                group_id="extra",
            ),
            render_group(
                flat["province"],
                "province",
                filled=True,
                group_id="province",
            ),
            render_group(
                national_feats,
                "national",
                filled=False,
                group_id="national",
            ),
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
